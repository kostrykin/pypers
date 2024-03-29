import time
import weakref
import os
import re

from typing import (
    Union,
    Optional
)
from collections.abc import Sequence

from .config import Config
from .output import (
    Output,
    get_output
)


def suggest_stage_id(class_name: str) -> str:
    """
    Suggest stage ID based on a class name.

    This function validates the class name, then finds and groups tokens in the class name.
    Tokens are grouped if they are consecutive and alphanumeric, but do not start with numbers.
    The function then converts the tokens to lowercase, removes underscores, and joins them with hyphens.

    :param class_name: The name of the class to suggest a configuration namespace for.
    :type class_name: str
    :return: A string of hyphen-separated tokens from the class name.
    :rtype: str
    :raises AssertionError: If the class name is not valid.
    """
    assert class_name != '_' and re.match('[a-zA-Z]', class_name) and re.match('^[a-zA-Z_](?:[a-zA-Z0-9_])*$', class_name), f'not a valid class name: "{class_name}"'
    tokens1 = re.findall('[A-Z0-9][^A-Z0-9_]*', class_name)
    tokens2 = list()
    i1 = 0
    while i1 < len(tokens1):
        token = tokens1[i1]
        i1 += 1
        if len(token) == 1:
            for t in tokens1[i1:]:
                if len(t) == 1 and (token.isnumeric() == t.isnumeric() or token.isalpha() == t.isalpha()):
                    token += t
                    i1 += 1
                else:
                    break
        tokens2.append(token.lower().replace('_', ''))
    if len(tokens2) >= 2 and tokens2[-1] == 'stage': tokens2 = tokens2[:-1]
    return '-'.join(tokens2)


class Stage(object):
    """
    A pipeline stage.

    Each stage can be controlled by a separate set of hyperparameters. Refer to the documentation of the respective pipeline stages for details. Most hyperparameters reside in namespaces, which are uniquely associated with the corresponding pipeline stages.

    :param name: Readable identifier of this stage.
    :param id: The stage ID, used as the hyperparameter namespace. Defaults to the result of the :py:meth:`~.suggest_stage_id` function if not specified.
    :param inputs: List of inputs required by this stage.
    :param outputs: List of outputs produced by this stage.

    Automation
    ^^^^^^^^^^

    Hyperparameters can be set automatically using the :py:meth:`~.configure` method.

    Inputs and outputs
    ^^^^^^^^^^^^^^^^^^

    Each stage must declare its required inputs and the outputs it produces. These are used by :py:meth:`~.create_pipeline` to automatically determine the stage order. The input ``input`` is provided by the pipeline itself.
    """

    inputs   = []
    outputs  = []
    consumes = []
    enabled_by_default = True

    def __init__(self):
        self.id       = type(self).id if hasattr(type(self), 'id') else suggest_stage_id(type(self).__name__)
        self.inputs   = frozenset(type(self).inputs) | frozenset(type(self).consumes)
        self.outputs  = frozenset(type(self).outputs)
        self.consumes = frozenset(type(self).consumes)
        self.enabled_by_default = type(self).enabled_by_default
        assert not self.id.endswith('+'), 'the suffix "+" is reserved as an indication of "the stage after that stage"'
        self._callbacks = {}

    def _callback(self, name, *args, **kwargs):
        if name in self._callbacks:
            for cb in self._callbacks[name]:
                cb(self, name, *args, **kwargs)

    def add_callback(self, name, cb):
        if name == 'after':
            self.add_callback( 'end', cb)
            self.add_callback('skip', cb)
        else:
            if name not in self._callbacks: self._callbacks[name] = []
            self._callbacks[name].append(cb)

    def remove_callback(self, name, cb):
        if name == 'after':
            self.remove_callback( 'end', cb)
            self.remove_callback('skip', cb)
        else:
            if name in self._callbacks: self._callbacks[name].remove(cb)

    def __call__(self, data, cfg, out=None, log_root_dir=None, **kwargs):
        out = get_output(out)
        cfg = cfg.get(self.id, {})
        if cfg.get('enabled', self.enabled_by_default):
            out.intermediate(f'Starting stage "{self.id}"')
            self._callback('start', data, out = out, **kwargs)
            input_data = {key: data[key] for key in self.inputs}
            clean_cfg = cfg.copy()
            clean_cfg.pop('enabled', None)
            t0 = time.time()
            output_data = self.process(cfg=clean_cfg, log_root_dir=log_root_dir, out=out, **input_data)
            dt = time.time() - t0
            assert len(set(output_data.keys()) ^ set(self.outputs)) == 0, 'stage "%s" produced spurious or missing output' % self.id
            data.update(output_data)
            for key in self.consumes: del data[key]
            self._callback('end', data, out = out, **kwargs)
            return dt
        else:
            out.write(f'Skipping disabled stage "{self.id}"')
            self._callback('skip', data, out = out, **kwargs)
            return 0
        
    def skip(self, data, out = None, **kwargs):
        self._callback('skip', data, out = out, **kwargs)

    def process(self, cfg: Optional[Config]=None, log_root_dir: Optional[str]=None, out :Optional[Output]=None, **inputs):
        """
        Executes the current pipeline stage.

        This method runs the current stage of the pipeline with the provided inputs, configuration parameters, and logging settings. It then returns the outputs produced by this stage.

        :param input_data: A dictionary containing the inputs required by this stage. Each key-value pair in the dictionary represents an input name and its corresponding value.
        :type input_data: dict
        :param cfg: A dictionary containing the hyperparameters to be used by this stage. Each key-value pair in the dictionary represents a hyperparameter name and its corresponding value.
        :type cfg: dict
        :param log_root_dir: The path to the directory where log files will be written. If this parameter is ``None``, no log files will be written.
        :type log_root_dir: str, optional
        :param out: An instance of a subclass of :py:class:`~pypers.output.Output` to handle the output of this stage. If this parameter is ``'muted'``, no output will be produced. If this parameter is ``None``, the default output handler will be used.
        :type out: :py:class:`~pypers.output.Output`, 'muted', or None, optional
        :return: A dictionary containing the outputs produced by this stage. Each key-value pair in the dictionary represents an output name and its corresponding value.
        :rtype: dict
        """
        raise NotImplementedError()

    def configure(self, *args, **kwargs):
        # FIXME: add documentation
        return dict()

    def __str__(self):
        return self.id

    def __repr__(self):
        return f'<{type(self).__name__}, id: {self.id}>'


class ProcessingControl:
    """
    A class used to control the processing of stages in a pipeline.

    This class keeps track of the first and last stages of a pipeline, and determines whether a given stage should be processed based on its position in the pipeline.

    :param first_stage: The first stage of the pipeline. Processing starts from this stage. If None, processing starts from the beginning.
    :type first_stage: str, optional
    :param last_stage: The last stage of the pipeline. Processing stops after this stage. If None, processing goes until the end.
    :type last_stage: str, optional
    """

    def __init__(self, first_stage: Optional[str]=None, last_stage: Optional[str]=None):
        self.started     = True if first_stage is None else False
        self.first_stage = first_stage
        self.last_stage  =  last_stage
    
    def step(self, stage):
        """
        Determines whether the given stage should be processed.

        If the stage is the first stage of the pipeline, processing starts. If the stage is the last stage of the pipeline, processing stops after this stage.

        :param stage: The stage to check.
        :type stage: str
        :return: True if the stage should be processed, False otherwise.
        :rtype: bool
        """
        if not self.started and stage == self.first_stage: self.started = True
        do_step = self.started
        if stage == self.last_stage: self.started = False
        return do_step


def _create_config_entry(cfg, key, factor, default_user_factor, type=None, min=None, max=None):
    keys = key.split('/')
    af_key = f'{"/".join(keys[:-1])}/AF_{keys[-1]}'
    cfg.set_default(key, factor * cfg.get(af_key, default_user_factor), True)
    if type is not None: cfg.update(key, func=type)
    if  min is not None: cfg.update(key, func=lambda value: __builtins__.max((value, min)))
    if  max is not None: cfg.update(key, func=lambda value: __builtins__.min((value, max)))


class Configurator:
    """
    Automatically configures hyperparameters of a pipeline.
    
    :param pipeline: An instance of the `Pipeline` class.
    :type pipeline: Pipeline
    """

    def __init__(self, pipeline: 'Pipeline'):
        assert pipeline is not None
        self._pipeline = weakref.ref(pipeline)

    @property
    def pipeline(self):
        """
        Get the pipeline associated with this configurator.
        
        :return: The pipeline instance.
        :rtype: Pipeline
        """
        pipeline = self._pipeline()
        assert pipeline is not None
        return pipeline
    
    def configure(self, base_cfg, input):
        """
        Configure the hyperparameters of the pipeline.
        
        :param base_cfg: The base configuration.
        :type base_cfg: Config
        :param input: The input data.
        :type input: Any
        :return: The configured hyperparameters.
        :rtype: Config
        """
        return self.pipeline.configure(base_cfg, input)
    
    def first_differing_stage(self, config1: 'Config', config2: 'Config'):
        """
        Find the first stage with differing configurations between two sets of hyperparameters.
        
        :param config1: The first set of hyperparameters.
        :type config1: Config
        :param config2: The second set of hyperparameters.
        :type config2: Config
        :return: The first differing stage, or None if no differences are found.
        :rtype: Stage or None
        """
        for stage in self.pipeline.stages:
            if any([
                stage.id in config1 and stage.id not in config2,
                stage.id not in config1 and stage.id in config2,
                stage.id in config1 and stage.id in config2 and config1[stage.id] != config2[stage.id],
            ]):
                return stage
        return None


class Pipeline:
    """
    Defines a processing pipeline.

    This class defines a processing pipeline that consists of multiple stages. Each stage performs a specific operation on the input data. The pipeline processes the input data by executing the `process` method of each stage successively.

    Note that hyperparameters are *not* set automatically if the :py:meth:`~.process_image` method is used directly. Hyperparameters are only set automatically if the :py:mod:`~.configure` method or batch processing is used.

    :param configurator: An instance of the `Configurator` class used to automatically configure hyperparameters of the pipeline. If not provided, a default `Configurator` instance will be created.
    :type configurator: Configurator, optional
    """
    
    def __init__(self, configurator: Optional[Configurator] = None):
        self.stages = []
        self.configurator = configurator if configurator else Configurator(self)

    def process(self, input, cfg, first_stage=None, last_stage=None, data=None, log_root_dir=None, out=None, **kwargs):
        """
        Processes the input.

        The :py:meth:`~.Stage.process` methods of the stages of the pipeline are executed successively.

        :param input: The input to be processed (can be ``None`` if and only if ``data`` is not ``None``).
        :param cfg: A :py:class:`~pypers.config.Config` object which represents the hyperparameters.
        :param first_stage: The name of the first stage to be executed.
        :param last_stage: The name of the last stage to be executed.
        :param data: The results of a previous execution.
        :param log_root_dir: Path to a directory where log files should be written to.
        :param out: An instance of an :py:class:`~pypers.output.Output` sub-class, ``'muted'`` if no output should be produced, or ``None`` if the default output should be used.
        :return: Tuple ``(data, cfg, timings)``, where ``data`` is the *pipeline data object* comprising all final and intermediate results, ``cfg`` are the finally used hyperparameters, and ``timings`` is a dictionary containing the execution time of each individual pipeline stage (in seconds).

        The parameter ``data`` is used if and only if ``first_stage`` is not ``None``. In this case, the outputs produced by the stages of the pipeline which are being skipped must be fed in using the ``data`` parameter obtained from a previous execution of this method.
        """
        cfg = cfg.copy()
        if log_root_dir is not None: os.makedirs(log_root_dir, exist_ok=True)
        if first_stage == self.stages[0].id and data is None: first_stage = None
        if first_stage is not None and first_stage.endswith('+'): first_stage = self.stages[1 + self.find(first_stage[:-1])].id
        if first_stage is not None and last_stage is not None and self.find(first_stage) > self.find(last_stage): return data, cfg, {}
        if first_stage is not None and first_stage != self.stages[0].id and data is None: raise ValueError('data argument must be provided if first_stage is used')
        if data is None: data = dict()
        if input is not None: data['input'] = input
        extra_stages = self.get_extra_stages(first_stage, last_stage, data.keys())
        out  = get_output(out)
        ctrl = ProcessingControl(first_stage, last_stage)
        timings = {}
        for stage in self.stages:
            if ctrl.step(stage.id) or stage.id in extra_stages:
                try:
                    dt = stage(data, cfg, out=out, log_root_dir=log_root_dir, **kwargs)
                except:
                    print(f'An error occured while executing the stage: {str(stage)}')
                    raise
                timings[stage.id] = dt
            else:
                stage.skip(data, out = out, **kwargs)
        return data, cfg, timings
    
    def get_extra_stages(self, first_stage, last_stage, available_inputs):
        required_inputs, available_inputs = set(), set(available_inputs) | {'input'}
        stage_by_output = dict()
        extra_stages    = list()
        ctrl = ProcessingControl(first_stage, last_stage)
        for stage in self.stages:
            stage_by_output.update({output: stage for output in stage.outputs})
            if ctrl.step(stage.id):
                required_inputs  |= stage.inputs
                available_inputs |= stage.outputs
        while True:
            missing_inputs = required_inputs - available_inputs
            if len(missing_inputs) == 0: break
            extra_stage = stage_by_output[list(missing_inputs)[0]]
            required_inputs  |= extra_stage.inputs
            available_inputs |= extra_stage.outputs
            extra_stages.append(extra_stage.id)
        return extra_stages

    def find(self, stage_id, not_found_dummy=float('inf')):
        """
        Returns the position of the stage identified by ``stage_id``.

        Returns ``not_found_dummy`` if the stage is not found.
        """
        try:
            return [stage.id for stage in self.stages].index(stage_id)
        except ValueError:
            return not_found_dummy
        
    def stage(self, stage_id):
        idx = self.find(stage_id, None)
        return self.stages[idx] if idx is not None else None

    def append(self, stage: 'Stage', after: Union[str, int] = None):
        for stage2 in self.stages:
            if stage2 is stage: raise RuntimeError(f'stage {stage.id} already added')
            if stage2.id == stage.id: raise RuntimeError(f'stage with ID {stage.id} already added')
        if after is None:
            self.stages.append(stage)
            return len(self.stages) - 1
        else:
            if isinstance(after, str): after = self.find(after)
            assert -1 <= after < len(self.stages)
            self.stages.insert(after + 1, stage)
            return after + 1

    def configure(self, base_cfg, *args, **kwargs):
        """
        Automatically configures hyperparameters.
        """
        cfg = base_cfg.copy()
        for stage in self.stages:
            specs = stage.configure(*args, **kwargs)
            for key, spec in specs.items():
                assert len(spec) in (2,3), f'{type(stage).__name__}.configure returned tuple of unknown length ({len(spec)})'
                _create_config_entry_kwargs = dict() if len(spec) == 2 else spec[-1]
                _create_config_entry(cfg, f'{stage.id}/{key}', *spec[:2], **_create_config_entry_kwargs)
        return cfg
    
    @property
    def fields(self):
        fields = set(['input'])
        for stage in self.stages:
            fields |= stage.outputs
        return fields


def create_pipeline(stages: Sequence):
    """
    Creates and returns a new :py:class:`.Pipeline` object configured for the given stages.

    The stage order is determined automatically.
    """
    available_inputs = set(['input'])
    remaining_stages = list(stages)

    # Ensure that the stage identifiers are unique
    ids = [stage.id for stage in stages]
    assert len(ids) == len(frozenset(ids)), 'ambiguous stage identifiers'

    # Ensure that no output is produced more than once
    outputs = list(available_inputs) + sum((list(stage.outputs) for stage in stages), [])
    assert len(outputs) == len(frozenset(outputs)), 'ambiguous outputs'

    pipeline = Pipeline()
    while len(remaining_stages) > 0:
        next_stage = None

        # Ensure that the next stage has no missing inputs
        for stage1 in remaining_stages:
            if stage1.inputs.issubset(available_inputs):
                conflicted = False

                # Ensure that no remaining stage requires a consumed input
                for stage2 in remaining_stages:
                    if stage1 is stage2: continue
                    if len(stage1.consumes) > 0 and stage1.consumes.issubset(stage2.inputs):
                        conflicted = True

                if not conflicted:
                    next_stage = stage1
                    break

        if next_stage is None:
            raise RuntimeError(f'failed to resolve total ordering (pipeline so far: {pipeline.stages}, available inputs: {available_inputs}, remaining stages: {remaining_stages})')
        remaining_stages.remove(next_stage)
        pipeline.append(next_stage)
        available_inputs |= next_stage.outputs
        available_inputs -= next_stage.consumes

    return pipeline