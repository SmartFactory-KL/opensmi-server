# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Server implementation of Skill-based Production using OPC-UA."""

from importlib import metadata

from open_smi_server import mixins as mixins
from open_smi_server._server import Server as Server
from open_smi_server._server import get_server as get_server
from open_smi_server.access_control import AccessControl as AccessControl
from open_smi_server.base_callable import BaseCallable as BaseCallable
from open_smi_server.base_machine import BaseMachine as BaseMachine
from open_smi_server.base_machinery_item import BaseComponent as BaseComponent
from open_smi_server.base_machinery_item import BaseMachineryItem as BaseMachineryItem
from open_smi_server.base_method import BaseMethod as BaseMethod
from open_smi_server.base_skill import BaseSkill as BaseSkill
from open_smi_server.lock import Lock as Lock
from open_smi_server.mixins.skill_logic_mixins import BaseSkillFinalResultData as BaseSkillFinalResultData
from open_smi_server.mixins.skill_logic_mixins import ContinuousSkillLogicMixin, FiniteSkillLogicMixin
from open_smi_server.resource import Resource as Resource
from open_smi_server.spatial_object import SpatialObject as SpatialObject
from open_smi_server.ua_fsm import UaFiniteStateMachine as UaFiniteStateMachine
from open_smi_server.ua_object import UaObject as UaObject
from open_smi_server.ua_object_containers import Components as Components
from open_smi_server.ua_object_containers import MethodSet as MethodSet
from open_smi_server.ua_object_containers import Resources as Resources
from open_smi_server.ua_object_containers import SkillSet as SkillSet
from open_smi_server.ua_variable import UaVariable as UaVariable
from open_smi_server.ua_variable_containers import Attributes as Attributes
from open_smi_server.ua_variable_containers import FinalResultData as FinalResultData
from open_smi_server.ua_variable_containers import Monitoring as Monitoring
from open_smi_server.ua_variable_containers import ParameterSet as ParameterSet
from open_smi_server.user import User as User


class BaseSkillFinite(FiniteSkillLogicMixin, BaseSkill):
    """Abstract base class for finite skills with behavior implemented in Python."""


class BaseSkillContinuous(ContinuousSkillLogicMixin, BaseSkill):
    """Abstract base class for continuous skills with behavior implemented in Python."""


__version__ = metadata.version("OpenSMI-Server")
