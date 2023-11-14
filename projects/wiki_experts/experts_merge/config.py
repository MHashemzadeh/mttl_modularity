import os
import sys
from projects.wiki_experts.src.config import ExpertConfig

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from mttl.datamodule.flan_tasks import FLAN_SUB19, FLAN_TASKS


class ExpertsMergeConfig(ExpertConfig):
    def _set_defaults(self):
        super()._set_defaults()

        ### merging exps
        self.action = "route"
        self.init_ng_oracle = False
        self.subsample_ng_train_set = -1
        self.use_vllm = False
        self.use_loss = False
        self.regularizer_factor = 0.0
        self.module_dict = None
        self.expert_routing = None
        self.n_ng_iterations = 2
        self.n_active_iterations = 1

        self.train_split = "test"
        self.test_split = "test"
        self.new_module_action = "replace"  # or add, None
        self.eval_metric = "rougeL"  # acc , loss

        self.dataset_test = "mmlu"  # dataset to use for testing
        self.modules_dir = os.environ.get("MODULES_DIR", "amlt/")

        self.finetune_new_expert = False

    def post_init(self):
        super().post_init()

        if self.finetune_task_name == "FLAN_SUB19":
            self.finetune_task_name = FLAN_SUB19
        elif self.finetune_task_name == "FLAN_ALL":
            self.finetune_task_name = FLAN_TASKS
