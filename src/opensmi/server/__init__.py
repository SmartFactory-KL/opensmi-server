# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Server implementation of Skill-based Production using OPC-UA."""

from importlib import metadata

from opensmi.server import mixins as mixins
from opensmi.server._server import Server as Server
from opensmi.server._server import get_server as get_server
from opensmi.server.access_control import AccessControl as AccessControl
from opensmi.server.base_callable import BaseCallable as BaseCallable
from opensmi.server.base_machine import BaseMachine as BaseMachine
from opensmi.server.base_machinery_item import BaseComponent as BaseComponent
from opensmi.server.base_machinery_item import BaseMachineryItem as BaseMachineryItem
from opensmi.server.base_method import BaseMethod as BaseMethod
from opensmi.server.base_skill import BaseSkill as BaseSkill
from opensmi.server.lock import Lock as Lock
from opensmi.server.mixins.skill_logic_mixins import BaseSkillFinalResultData as BaseSkillFinalResultData
from opensmi.server.mixins.skill_logic_mixins import ContinuousSkillLogicMixin, FiniteSkillLogicMixin
from opensmi.server.resource import Resource as Resource
from opensmi.server.spatial_object import SpatialObject as SpatialObject
from opensmi.server.ua_fsm import UaFiniteStateMachine as UaFiniteStateMachine
from opensmi.server.ua_object import UaObject as UaObject
from opensmi.server.ua_object_containers import Components as Components
from opensmi.server.ua_object_containers import MethodSet as MethodSet
from opensmi.server.ua_object_containers import Resources as Resources
from opensmi.server.ua_object_containers import SkillSet as SkillSet
from opensmi.server.ua_variable import UaVariable as UaVariable
from opensmi.server.ua_variable_containers import Attributes as Attributes
from opensmi.server.ua_variable_containers import FinalResultData as FinalResultData
from opensmi.server.ua_variable_containers import Monitoring as Monitoring
from opensmi.server.ua_variable_containers import ParameterSet as ParameterSet
from opensmi.server.user import User as User


class BaseSkillFinite(FiniteSkillLogicMixin, BaseSkill):
    """Abstract base class for finite skills with behavior implemented in Python."""


class BaseSkillContinuous(ContinuousSkillLogicMixin, BaseSkill):
    """Abstract base class for continuous skills with behavior implemented in Python."""


__version__ = metadata.version("OpenSMI-Server")
