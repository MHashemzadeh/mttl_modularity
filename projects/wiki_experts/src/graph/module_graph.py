import re
import torch
from typing import Dict
import sys
import os
import re
from string import Template
from collections import defaultdict

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
from mttl.models.utils import download_from_hub
from mttl.utils import get_checkpoint_path, logger
from projects.wiki_experts.src.config import ExpertConfig
from dataclasses import dataclass
from typing import Union


@dataclass
class Expert:
    expert_config: ExpertConfig
    expert_weights: Dict[str, torch.Tensor]


class Node:
    def __init__(self, name):
        self.name = name
        self.children = []
        self._cached_instantiation = None

    def get_name(self, **kwargs):
        return self.name

    @classmethod
    def from_args(cls, name, graph, args=None):
        return Node(name)

    def collect_variables(self):
        vars = []
        if hasattr(self, "variables"):
            vars += self.variables
        if not self.children:
            return vars
        for child in self.children:
            vars += child.collect_variables()
        return vars

    def instantiate(self, *args, **kwargs):
        if self._cached_instantiation is not None:
            return self._cached_instantiation

        assert (
            len(self.children) <= 1
        ), "Node can only have one child for now, use operators instead."

        instantiation = []
        if not self.children:
            # consider this to be a leaf node
            instantiation = [load_expert(self.name)]
        else:
            instantiation = [self.children[0].instantiate(*args, **kwargs)[0]]
        return instantiation

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.name


class OperatorNode(Node):
    def __init__(self, name):
        super().__init__(name)

    @classmethod
    def from_args(cls, args, graph):
        raise NotImplementedError


class LinearNode(OperatorNode):
    @classmethod
    def from_args(cls, name, graph, args=None):
        variable_names = re.findall(r"\$([a-zA-Z_][a-zA-Z0-9_]*)", name)
        for i, var_name in enumerate(variable_names):
            # replace variable names with their position
            name = re.sub(f"\${var_name}", f"${i}", name, count=1)
        node = LinearNode(name)
        node.weights = {}
        node.variables = []

        node_args_pairs = args.split(",")
        for i, pair in enumerate(node_args_pairs):
            child_name, weight = pair.split(":")
            node.children.append(graph.get_or_create_node(child_name.strip()))
            # node.weights.append(float(weight.strip()))
            weight = weight.strip()
            if "$" not in weight:
                node.weights[child_name] = float(weight)
            else:
                node.variables.append(f"{name}[{i}]")

        return node

    def get_name(self, **kwargs):
        if len(kwargs) == 0 or len(self.variables) == 0 or "$" not in self.name:
            return self.name
        name = self.name
        for i, v in enumerate(self.variables):
            name = name.replace(f"${i}", str(kwargs[v]))

        return name

    def instantiate(self, *args, **kwargs):
        if self._cached_instantiation is not None:
            return self._cached_instantiation

        instantiation = {}
        first_module = None
        for node in self.children:
            instantiation[node.name] = node.instantiate(*args, **kwargs)[0]
            first_module = (
                instantiation[node.name] if first_module is None else first_module
            )

        # now, merge with a given importance weight
        assert len(instantiation) == len(self.weights) + len(self.variables)

        merged_weights = {}
        for i, (name, expert) in enumerate(instantiation.items()):
            if name in self.weights:
                weight = self.weights[name]
            else:
                param_name = f"{self.name}[{i}]"
                weight = kwargs.get(param_name, None)
                assert (
                    weight is not None
                ), f"Must pass the weight for node {param_name} to be able to instantiate"

            for k, v in expert.expert_weights.items():
                value = v * torch.tensor(weight, dtype=v.dtype)
                if k in merged_weights:
                    merged_weights[k] += value
                else:
                    merged_weights[k] = value

        return [
            Expert(
                expert_config=first_module.expert_config,
                expert_weights=merged_weights,
            )
        ]

    def __repr__(self):
        return "linear({})".format(
            ", ".join(["{}:{}".format(n, w) for n, w in zip(self.nodes, self.weights)])
        )


class ModuleGraph:
    # Operator-to-class mapping
    OPERATOR_CLASSES = {None: Node, "linear": LinearNode}

    def __init__(self):
        self.nodes = {}

    def get_or_create_node(self, node_name, node_type=None, args=None):
        if node_name not in self.nodes:
            node_class = self.OPERATOR_CLASSES[node_type]
            self.nodes[node_name] = node_class.from_args(node_name, self, args)
        return self.nodes[node_name]

    def dumps(self, **kwargs):
        graph_str = []
        for node_name, node in self.nodes.items():
            if not node.children:
                continue
            if isinstance(node, OperatorNode):
                continue
            graph_str.append(
                "{} -> {}".format(
                    node_name, ", ".join([n.get_name(**kwargs) for n in node.children])
                )
            )
        return "; ".join(graph_str)

    @classmethod
    def from_string(self, s):
        graph = ModuleGraph()
        parts = [p.strip() for p in s.split(";")]

        for part in parts:
            if "->" in part:
                source, targets = part.split("->")
                targets = targets.strip()
                source = source.strip()

                match_source = re.match(r"(\w+)\((.+)\)", source.strip())
                if match_source:
                    raise ValueError("Source cannot be an operator.")

                match_target = re.match(r"(\w+)\((.+)\)", targets.strip())
                source_node = graph.get_or_create_node(source)

                if match_target:  # This means there's an operator
                    operator = match_target.group(1)
                    args = match_target.group(2)

                    if operator not in self.OPERATOR_CLASSES:
                        raise ValueError(
                            f"Unknown operator: '{operator}' in segment '{part}'"
                        )

                    children = [
                        graph.get_or_create_node(
                            node_name=targets, node_type=operator, args=args
                        )
                    ]
                else:
                    children = [
                        graph.get_or_create_node(t.strip()) for t in targets.split(",")
                    ]
                source_node.children.extend(children)
        return graph

    @property
    def roots(self):
        parent_nodes = {}
        for _, parent_node in self.nodes.items():
            for children in parent_node.children:
                parent_nodes[children] = parent_node
        return set(self.nodes.values()) - set(parent_nodes.keys())

    @property
    def leaves(self):
        children_nodes = set()
        for _, parent_node in self.nodes.items():
            if not parent_node.children:
                children_nodes.add(parent_node)
        return children_nodes

    def create_modules(self, *args, **kwargs):
        root_modules = {}
        for root in self.roots:
            root_modules[root.name] = root.instantiate(*args, **kwargs)[0]
        return root_modules

    def get_variables(self):
        variables = []
        for root in self.roots:
            variables += root.collect_variables()
        return variables


def load_expert(
    expert_path: str,
    expert_name: str = None,
):
    # load the expert weights
    import os

    if os.path.isfile(expert_path) or os.path.isdir(expert_path):
        expert_checkpoint = get_checkpoint_path(expert_path)
    else:
        expert_checkpoint = download_from_hub(expert_path)

    logger.info(f"Loading expert from {expert_checkpoint}...")
    expert_checkpoint = torch.load(expert_checkpoint, map_location="cpu")

    expert_config = ExpertConfig(
        kwargs=expert_checkpoint["hyper_parameters"], silent=True, raise_error=False
    )

    expert_name = expert_name or expert_config.expert_name
    if expert_name is None:
        if expert_config.finetune_task_name is not None:
            expert_name = expert_config.finetune_task_name
        else:
            expert_name = os.path.basename(expert_path)
        logger.info(
            "Assigning expert name, not found in checkpoint: {}".format(expert_name)
        )

    expert_config.expert_name = expert_name

    expert_weights = expert_checkpoint["state_dict"]
    expert_weights = {k.replace("model.", "", 1): v for k, v in expert_weights.items()}
    return Expert(expert_config, expert_weights)


if __name__ == "__main__":
    # Example usage:
    s = """
    security_studies -> B;
    B -> linear(sordonia/llama2-13b-platypus:0.5, sordonia/expert_llama2_13b_security_studies:3);
    C -> linear(B:0.5);
    default -> C
    """
    s = """    
    security_studies -> linear(sordonia/expert_llama2_13b_security_studies:5,sordonia/llama2-13b-platypus:$weight);
    security_studies2 -> linear(sordonia/expert_llama2_13b_security_studies:1);    
    security_studies3 -> linear(sordonia/expert_llama2_13b_security_studies:$weight_blabla);
    """

    graph = ModuleGraph.from_string(s)
    print(graph)
    print(graph.roots)
    print(graph.leaves)
    print(graph.dumps())
    vars = graph.get_variables()
    print(vars)
    print(
        graph.dumps(
            **{
                "linear(sordonia/expert_llama2_13b_security_studies:5,sordonia/llama2-13b-platypus:$0)[1]": 0,
                "linear(sordonia/expert_llama2_13b_security_studies:$0)[0]": 1,
            }
        )
    )
    print(graph.dumps(**{v: i for i, v in enumerate(vars)}))
    print(graph.create_modules(**{v: 1 for v in vars}).keys())
