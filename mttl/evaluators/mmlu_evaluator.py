from collections import defaultdict
from copy import deepcopy
import tqdm
import torch
import numpy as np
import pytorch_lightning as pl

from mttl.dataloader.ni_metrics import compute_metrics
from mttl.models.utils import transfer_batch_to_device
from mttl.evaluators.base import compute_task_aggregation


class MMLUEvaluator(object):
    def __init__(self, config, data_dir=None, num_pos_examples=0, device="cuda"):
        from mttl.datamodule.mmlu_data_module import MMLUDataModule

        self.config = deepcopy(config)
        self.device = device
        self.config.num_pos_examples = num_pos_examples

        if data_dir is None:
            data_dir = config.data_dir

        self.data_dir = data_dir
        self.datamodule = MMLUDataModule(
            self.config, data_dir=self.data_dir, for_generation=True
        )
        self.datamodule.setup("test")

    def evaluate(self, model, eval_batches=-1):
        was_train = model.training
        if was_train:
            model.eval()

        tokenizer = self.datamodule.tokenizer
        samples_seen = 0

        # DDP
        if hasattr(model, "module"):
            model = model.module

        all_predictions = []
        all_references = []
        task_names = []
        all_EM = []

        dataloader = self.datamodule.test_dataloader(shuffle=eval_batches > 0)

        if eval_batches == -1:
            eval_batches = len(dataloader)
        
        pbar = tqdm.tqdm(
            enumerate(dataloader),
            total=min(len(dataloader), eval_batches),
        )
        for step, batch in pbar:
            task_name = batch.pop("task_names", None)
            batch.pop("input_texts", None)
            labels_text = batch.pop("labels_texts", None)

            extra_kwargs = {}
            max_length = 5

            if self.config.model_family == "gpt":
                max_length += batch["input_ids"].shape[-1]
                extra_kwargs["pad_token_id"] = tokenizer.pad_token_id

            batch = transfer_batch_to_device(batch, self.device)
            with torch.no_grad():
                if isinstance(model, pl.LightningModule):
                    predictions = model.generate(
                        batch,
                        max_length=max_length,
                        generation_config=model.generation_config,
                        return_dict_in_generate=True,
                        output_scores=True,
                        **extra_kwargs,
                    )
                else:
                    predictions = model.generate(
                        input_ids=batch["input_ids"],
                        attention_mask=batch["attention_mask"],
                        max_length=max_length,
                        generation_config=model.generation_config,
                        return_dict_in_generate=True,
                        output_scores=True,
                        **extra_kwargs,
                    )

            logits = predictions.scores[0]
            probs = (
                torch.stack(
                    [
                        logits[:, tokenizer("A", add_special_tokens=False).input_ids[-1]],
                        logits[:, tokenizer("B", add_special_tokens=False).input_ids[-1]],
                        logits[:, tokenizer("C", add_special_tokens=False).input_ids[-1]],
                        logits[:, tokenizer("D", add_special_tokens=False).input_ids[-1]],
                    ],
                    dim=-1,
                )
                .max(dim=-1)[1]
                .detach()
                .cpu()
                .numpy()
            )
            predictions = [{0: "A", 1: "B", 2: "C", 3: "D"}[p] for p in probs]
            references = labels_text

            # If we are in a multiprocess environment, the last batch has duplicates
            if step == len(dataloader) - 1:
                predictions = predictions[: len(dataloader.dataset) - samples_seen]
                references = references[: len(dataloader.dataset) - samples_seen]
                task_name = task_name[: len(dataloader.dataset) - samples_seen]
            else:
                samples_seen += len(references)

            all_predictions += predictions
            all_references += references
            task_names += task_name

            eval_metrics = compute_metrics(
                predictions, [[r] for r in references], reduction="mean"
            )

            all_EM.append(eval_metrics["exact_match"])
            pbar.set_description(
                f"Task: {task_name[0] if task_name else None}, EM: {np.mean(all_EM):.4f}"
            )

            if step == eval_batches:
                break

        if was_train:
            model.train()

        eval_metrics = compute_metrics(
            all_predictions, [[r] for r in all_references], reduction="none"
        )
        return compute_task_aggregation(task_names, eval_metrics["exact_match"])
