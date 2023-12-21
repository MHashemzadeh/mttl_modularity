from mttl.datamodule.mbpp_datamodule import MBPPDataModule
from mttl.evaluators.code_evaluator import CodeEvaluator


class MBPPEvaluator(CodeEvaluator):
    def __init__(self, config, device="cuda", use_vllm=False, generation_kwargs=None):
        datamodule = MBPPDataModule(config, for_generation=True)

        super().__init__(
            datamodule=datamodule,
            device=device,
            use_vllm=use_vllm,
            generation_kwargs=generation_kwargs,
        )
