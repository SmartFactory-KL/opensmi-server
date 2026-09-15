# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

from __future__ import annotations

from opensmi.server.resource import ResourceDefinition

BATTERY_PACK = ResourceDefinition(
    asset_id="snr-BP10000-24-fffff",
    component_name="Battery_Pack",
    resource_class="Battery_Pack",
)

CAB_A_BLUE = ResourceDefinition(
    asset_id="snr-T10000-000ff",
    component_name="Cab_A_Blue",
    resource_class="Cab",
    form="A",
    color="Blue",
)
