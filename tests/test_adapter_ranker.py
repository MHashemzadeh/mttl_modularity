# unit test for adapter_ranker
import pytest
from mttl.datamodule.mt_seq_to_seq_module import FlanModule, FlanConfig
from projects.wiki_experts.src.expert_model import MultiExpertModelRanker
from projects.wiki_experts.src.config import ExpertConfig


def test_retrieval_routing():
    config = ExpertConfig()
    config.routing = "retrieval"
    config.num_labels = 246
    config.model = "EleutherAI/gpt-neo-125m"
    config.classifer_repo_id = "zhan1993/gpt-neo_classifer_ranker"

    config.module_graph = "adversarial_qa_dbert_answer_the_following_q -> linear(zhan1993/gpt-neo_adversarial_qa_dbert_answer_the_following_q:0);"
    finetune_task_name = "adversarial_qa_dbert_answer_the_following_q"
    data_module = FlanModule(
        FlanConfig(
            dataset="sordonia/flan-debug-flat",
            model="EleutherAI/gpt-neo-125m",
            finetune_task_name=finetune_task_name,
            predict_batch_size=1,
            include_template_type="*",
        ),
        for_generation=True,
    )

    module = MultiExpertModelRanker(**vars(config), tokenizer=data_module.tokenizer)
    module.load_from_graph_string(config.module_graph)
    batch = next(iter(data_module.val_dataloader()))

    prediction_experts = module.get_predicted_experts(batch)
    experts_selections = module.expert_retrieval(batch)
    assert len(prediction_experts) == 1
    assert prediction_experts[0] == "adversarial_qa_dbidaf_answer_the_following_q"
    assert experts_selections[0] == "adversarial_qa_dbert_answer_the_following_q"
