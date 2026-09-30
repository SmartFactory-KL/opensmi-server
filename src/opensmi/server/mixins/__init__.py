# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixins for `UaObject` and its subclasses.

Mixins add functionality to a component, skill, method, etc. by inheriting from them alongside the base class.
They are grouped by how essential they are.

**Note**: Inheritance order matters: mixins come first, the base class comes last.

Required functionality
======================

Every `BaseComponent` needs to implement the `AbstractUaLogger` interface. Use one of these mixins (never both) or
implement the interface yourself:

- `NotificationMixin`: The component owns a `Notification` child node and logs through it.
- `NotificationForwarderMixin`: The component forwards notifications to the parent, which must implement
  `AbstractUaLogger`. The root parent `BaseMachine` always fulfils this requirement.

Optional functionality
======================

These mixins are only needed if your class uses the corresponding feature. For example, not every component
has attributes, but if yours does, add the matching mixin.

- `LockableMixin` (`BaseComponent`): Adds a `Lock` for the component and its children. Without it, the lock of the
  parent is used. The root parent `BaseMachine` always provides a `Lock`.
- `RequirementsMixin` (`BaseSkill`, `BaseMethod`): Declare dependencies on other skills, etc. Only needed if your
  logic depends on them.
- `SpatialObjectListMixin` (`BaseMachine`): If your machine has a spatial object list.
- `SpatialObjectMixin` (`BaseComponent`): If your component has a spatial object.

Containers
----------

If your object has variables (`UaVariable`) or resources (`Resource`) in one of the standard containers,
add the mixin for that container:

- `AttributesMixin`: For the `Attributes` variable container.
- `FinalResultDataMixin`: For the `FinalResultData` variable container.
- `MonitoringMixin`: For the `Monitoring` variable container.
- `ParameterSetMixin`: For the `ParameterSet` variable container.
- `ResourcesMixin`: For the `Resources` resource container.

Optional typing
===============

These mixins add type hints and runtime type checking for child objects of `BaseComponent` and `BaseMachine`.
Nothing depends on them, but we highly recommend them for a better developer experience.

- `ComponentsMixin`: Typed ``components`` property.
- `MethodSetMixin`: Typed ``method_set`` property.
- `SkillSetMixin`: Typed ``skill_set`` property.
"""

# Note: only export mixins the end-user is supposed to use
from .lockable_mixin import LockableMixin as LockableMixin
from .notification_mixins import NotificationForwarderMixin as NotificationForwarderMixin
from .notification_mixins import NotificationMixin as NotificationMixin
from .parent_mixin import ParentMixin as ParentMixin
from .requirements_mixin import RequirementsMixin as RequirementsMixin
from .spatial_object_mixin import SpatialObjectListMixin as SpatialObjectListMixin
from .spatial_object_mixin import SpatialObjectMixin as SpatialObjectMixin
from .ua_object_container_mixins import ComponentsMixin as ComponentsMixin
from .ua_object_container_mixins import MethodSetMixin as MethodSetMixin
from .ua_object_container_mixins import ResourcesMixin as ResourcesMixin
from .ua_object_container_mixins import SkillSetMixin as SkillSetMixin
from .ua_variable_container_mixins import AttributesMixin as AttributesMixin
from .ua_variable_container_mixins import FinalResultDataMixin as FinalResultDataMixin
from .ua_variable_container_mixins import MonitoringMixin as MonitoringMixin
from .ua_variable_container_mixins import ParameterSetMixin as ParameterSetMixin
