import pytorch_lightning as pl
from projects.wiki_experts.src.ranker.classifier_ranker import (
    SentenceTransformerClassifier,
)
from mttl.datamodule.mt_seq_to_seq_module import (
    FlatMultiTaskConfig,
    FlatMultiTaskModule,
)
from projects.wiki_experts.src.ranker.config import RankerConfig
from projects.wiki_experts.src.ranker.clip_ranker import CLIPRanker, CLIPTripletRanker
from projects.wiki_experts.src.ranker.clip_data_module import (
    CLIPExpertsDatamodule,
    CLIPExpertsConfig,
    CLIPTripleDataModule,
)
import os
from pytorch_lightning import seed_everything


def train_triplet_clip(args):
    wandb_logger = None
    if os.environ.get("WANDB_API_KEY") or args.wandb_project:
        import wandb

        project = os.environ.get("WANDB_PROJECT", "wiki_experts")
        project = args.wandb_project if args.wandb_project is not None else project
        args.exp_name = "dev_run" if args.exp_name is None else args.exp_name
        wandb_logger = pl.loggers.WandbLogger(
            project=project,
            name=args.exp_name,  # , config=args_
            settings=wandb.Settings(start_method="fork"),
        )
        wandb_logger.experiment.save("*.py")
        wandb_logger.experiment.save("*/*.py")

    # test the model
    dataconfig = CLIPExpertsConfig(
        dataset=args.dataset,
        model=args.model,
        train_batch_size=args.train_batch_size,
        finetune_task_name=args.finetune_task_name,
        predict_batch_size=args.predict_batch_size,
    )
    datamodule = CLIPTripleDataModule(dataconfig)
    model = CLIPTripletRanker(expert_names=datamodule.task_names)

    # add model checkpoint
    checkpoint_callback = pl.callbacks.ModelCheckpoint(
        monitor="val/loss_epoch",
        dirpath=f"clip_ranker_{args.exp_name}/",
        filename="clip-{epoch:02d}-{val/loss:.2f}",
        save_top_k=1,
        mode="min",
    )

    trainer = pl.Trainer(
        max_epochs=args.num_train_epochs,
        accelerator="gpu",
        callbacks=[checkpoint_callback],
        devices=1,
        logger=wandb_logger,
        val_check_interval=0.25,
    )
    trainer.fit(model, datamodule)
    if wandb_logger:
        wandb_logger.experiment.finish()


def train_clip(args):
    wandb_logger = None
    if os.environ.get("WANDB_API_KEY") or args.wandb_project:
        import wandb

        project = os.environ.get("WANDB_PROJECT", "wiki_experts")
        project = args.wandb_project if args.wandb_project is not None else project
        args.exp_name = "dev_run" if args.exp_name is None else args.exp_name
        wandb_logger = pl.loggers.WandbLogger(
            project=project,
            name=args.exp_name,  # , config=args_
            settings=wandb.Settings(start_method="fork"),
        )
        wandb_logger.experiment.save("*.py")
        wandb_logger.experiment.save("*/*.py")
    model = CLIPRanker()

    # test the model
    dataconfig = CLIPExpertsConfig(
        dataset=args.dataset,
        model=args.model,
        train_batch_size=args.train_batch_size,
        finetune_task_name=args.finetune_task_name,
        predict_batch_size=args.predict_batch_size,
    )
    datamodule = CLIPExpertsDatamodule(dataconfig)

    # add model checkpoint
    checkpoint_callback = pl.callbacks.ModelCheckpoint(
        monitor="val/loss_epoch",
        dirpath=f"clip_ranker_{args.exp_name}/",
        filename="clip-{epoch:02d}-{val/loss:.2f}",
        save_top_k=1,
        mode="min",
    )

    trainer = pl.Trainer(
        max_epochs=args.num_train_epochs,
        accelerator="gpu",
        callbacks=[checkpoint_callback],
        devices=1,
        logger=wandb_logger,
        val_check_interval=0.25,
    )
    trainer.fit(model, datamodule)
    if wandb_logger:
        wandb_logger.experiment.finish()


def train_classifier(args):
    # using wandb project
    seed_everything(args.seed, workers=True)
    wandb_logger = None
    if os.environ.get("WANDB_API_KEY") or args.wandb_project:
        import wandb

        project = os.environ.get("WANDB_PROJECT", "wiki_experts")
        project = args.wandb_project if args.wandb_project is not None else project
        args.exp_name = "dev_run" if args.exp_name is None else args.exp_name
        wandb_logger = pl.loggers.WandbLogger(
            project=project,
            name=args.exp_name,  # , config=args_
            settings=wandb.Settings(start_method="fork"),
        )
        wandb_logger.experiment.save("*.py")
        wandb_logger.experiment.save("*/*.py")

    # train the classifier
    if "flat" not in args.dataset:
        raise ValueError("Only flat datamodule supported for now.")

    config = FlatMultiTaskConfig(
        dataset=args.dataset,
        model=args.model,
        train_batch_size=args.train_batch_size,
        finetune_task_name=args.finetune_task_name,
    )
    datamodule = FlatMultiTaskModule(config)
    module = SentenceTransformerClassifier(task_names=datamodule.task_names)

    # add model checkpoint

    datapath = f"classification_ranker_{args.dataset}"
    checkpoint_callback = pl.callbacks.ModelCheckpoint(
        monitor="val/loss_epoch",
        dirpath=datapath,
        filename="classifier-{epoch:02d}-{val/loss:.2f}",
        save_top_k=1,
        mode="min",
    )

    trainer = pl.Trainer(
        max_epochs=args.num_train_epochs,
        accelerator="gpu",
        callbacks=[checkpoint_callback],
        devices=1,
        logger=wandb_logger,
    )
    trainer.fit(module, datamodule)
    if wandb_logger:
        wandb_logger.experiment.finish()


if __name__ == "__main__":
    args = RankerConfig.parse()
    if args.ranker_model == "classifier":
        train_classifier(args)
    elif args.ranker_model == "clip":
        train_clip(args)
    elif args.ranker_model == "clip_triplet":
        train_triplet_clip(args)

    from projects.wiki_experts.src.ranker.adapter_ranker import AdapterRankerHelper

    expert_ranker = AdapterRankerHelper(
        ranker_model="classifier",
        ranker_path="/projects/futhark1/data/wzm289/code/lucas_mttl/projects/wiki_experts/classification_ranker_sordonia/adauni-v1-flat/classifier-epoch=00-val/loss=6.02.ckpt",
    )

    config = FlatMultiTaskConfig(
        dataset=args.dataset,
        model=args.model,
        train_batch_size=args.train_batch_size,
        finetune_task_name=args.finetune_task_name,
    )
    datamodule = FlatMultiTaskModule(config)
    dataset = datamodule.val_dataloader()
    batch = next(iter(dataset))
    print(expert_ranker.predict_batch(batch))
