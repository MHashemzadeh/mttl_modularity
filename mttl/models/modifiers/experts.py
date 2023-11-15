import re
import torch
from torch import nn
from typing import Any, Dict
from mttl.models.modifiers.base import MergeableAdapter
from mttl.models.modifiers.lora import LoRA, SkilledLoRA
from mttl.models.modifiers.llama_adapter import (
    KVAdapter,
    ParallelKVAdapters,
    ConcatKVAdapters,
)
from mttl.models.modifiers.expert_routing import Router
from mttl.utils import logger


def add_expert_to_transformer(
    transformer,
    expert_name,
    expert_config,
    expert_weights,
    action="route",
    is_default=False,
    load_only_layers=None,
    selectors={},
    global_config=None,
):
    # create a shared container for the task id
    if not hasattr(transformer, "task_id_container"):
        transformer.task_id_container = {}

    total_layers = 0
    added_layers = []

    for m_name, module in dict(transformer.named_modules()).items():
        if re.fullmatch(expert_config.modify_modules, m_name):
            for c_name, layer in dict(module.named_children()).items():
                if re.fullmatch(expert_config.modify_layers, c_name):
                    total_layers += 1
                    layer_name = f"{m_name}.{c_name}"
                    selector = None
                    if (
                        global_config.task_agnostic_routing
                        and "kv_adapter" in expert_config.model_modifier
                    ):
                        selector = 1

                    if type(layer) != ExpertContainer:
                        # create an expert lora container
                        expert_container = ExpertContainer(
                            expert_config,
                            transformer.task_id_container,
                            layer,
                            selector=selector,
                        )
                        expert_container.__layer_name__ = layer_name
                        setattr(
                            module,
                            c_name,
                            expert_container,
                        )
                    else:
                        expert_container = layer

                    # subset the relevant expert weights starting w __layer_name__
                    subset_expert_weights = {
                        k.replace(expert_container.__layer_name__ + ".", ""): v
                        for k, v in expert_weights.items()
                        if k.startswith(expert_container.__layer_name__)
                    }

                    layer_num = int(expert_container.__layer_name__.split(".")[2])

                    if load_only_layers:
                        pos = load_only_layers.find("-")
                        sel = int(load_only_layers.replace("-", ""))

                        if pos == 0:
                            # add until layer number excluded
                            if layer_num >= sel:
                                continue
                        else:
                            if layer_num < sel:
                                continue

                    added_layers.append(expert_container.__layer_name__)
                    expert_container.add_expert(
                        expert_name,
                        expert_config,
                        subset_expert_weights,
                        action=action,
                        is_default=is_default,
                    )

    logger.info("Adding expert to layers %s", added_layers)
    return transformer


class ExpertContainer(MergeableAdapter):
    def __init__(self, config, task_id_container, layer, selector=None):
        super().__init__()
        self.config = config
        self.layer = layer
        self.selector = selector

        if (
            not isinstance(self.layer, nn.Linear)
            and "kv_adapter" not in self.config.model_modifier
        ):
            raise ValueError(
                "Expert containers for layers other than nn.Linear have not been implemented."
            )

        self.info_container = task_id_container
        self.default_expert_name = None
        self.merged_expert_names = []
        self.experts = nn.ModuleDict({})

    def add_expert(
        self,
        name: str,
        expert_config: Any,
        expert_weights: Dict[str, torch.Tensor],
        action="merge",
        is_default=False,
    ) -> None:
        if name in self.experts:
            raise ValueError("An expert with name {} already exists.".format(name))

        if is_default and action == "merge":
            raise ValueError(
                "Cannot set is_default if this expert is merged, change to 'route'."
            )

        # hack this for now, but build a proper config for each module
        if expert_config.model_modifier == "lora":
            expert_module = LoRA(expert_config, self.layer)
        elif expert_config.model_modifier == "kv_adapter":
            expert_module = KVAdapter(expert_config, self.layer)
        else:
            raise NotImplementedError(
                "ExpertContainer only supports LoRA/KVAdapter experts."
            )

        expert_module.load_adapter_weights(expert_weights)

        if action == "merge":
            # weight is merged with layer so we can discard it now
            if expert_config.model_modifier == "lora":
                expert_module.merge_with_layer()
                self.merged_expert_names.append(name)
            else:
                raise NotImplementedError("Merging experts only supports LoRA experts.")
        else:
            # we keep track of the expert weights
            self.experts[name] = expert_module
        if is_default:
            self.default_expert_name = name

    def merge_with_layer(self):
        if len(self.experts) > 0:
            for name, expert_module in self.experts.items():
                assert isinstance(
                    expert_module, LoRA
                ), "Only LoRA experts can be merged with the layer for now."
                expert_module.merge_with_layer()
                self.merged_expert_names.append(name)
                self.experts.pop(name)

    def weighted_route(self, input, task_weights, **kwargs):
        """
        Route all examples according to the weights given in the weights dictionary: expert_name -> weight
        """
        load_experts = []
        weights = []

        for task_name, weight in task_weights.items():
            load_experts.append(self.experts[task_name])
            weights.append(weight)
        # assume all experts are loras
        output = SkilledLoRA.weighted_merge_forward(
            input, load_experts, weights, merge_after=True, **kwargs
        )
        return output

    def route_with_task_name(self, input, task_names, **kwargs):
        """
        Route according to the task name information
        """
        load_experts = []

        for task_name in task_names:
            if task_name not in self.experts:
                if not self.default_expert_name:
                    raise ValueError(
                        "The expert for this task {} does not exists. Consider setting a default expert!".format(
                            task_name
                        )
                    )
                else:
                    selected_expert = self.default_expert_name
            else:
                selected_expert = task_name
            load_experts.append(self.experts[selected_expert])

        # For now, let's assume that all experts are of the same type
        expert_cls = type(load_experts[0])
        assert all(isinstance(expert, expert_cls) for expert in load_experts)

        if expert_cls == LoRA:
            output = expert_cls.parallel_linear_forward(input, load_experts, **kwargs)
        elif expert_cls == KVAdapter:
            fused_adapter = ParallelKVAdapters(load_experts)
            output = fused_adapter(input, **kwargs)

        return output

    def forward(self, input, **kwargs):
        task_names = self.info_container["routing_infos"].task_names
        if task_names and (
            any(task_name not in self.experts for task_name in task_names)
            and not self.default_expert_name
            and len(self.experts)
        ):
            raise ValueError(
                "Experts for all tasks have not been loaded! Set a default expert?"
            )

        # if it has some routing experts *and* task names, then we can route
        if (
            len(self.experts)
            and self.selector is None
            and not self.config.task_agnostic_routing
        ):
            assert (
                task_names is not None
            ), "Task names are not given: set router or merge experts into the layer."
            output = self.route_with_task_name(input, task_names, **kwargs)
        elif len(self.experts) and self.selector is not None:
            if "kv_adapter" in self.config.model_modifier:
                fused_adapter = ConcatKVAdapters(self.experts.values())
                output = fused_adapter(input, **kwargs)
            else:
                weights: Dict = self.selector(input)
                output = self.weighted_route(input, weights, **kwargs)
        else:
            ###############################################################
            ## no experts -- no routing, experts were merged into the layer
            output = self.layer(input, **kwargs)
        return output
