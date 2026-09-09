from typing_extensions import override

from open_smi_server import BaseSkillFinalResultData
from open_smi_server.base_startup_skill import BaseStartupSkill
from open_smi_server.mixins import FinalResultDataMixin


class StartupSkill(BaseStartupSkill, FinalResultDataMixin[BaseSkillFinalResultData]):
    def __init__(self, **kwargs) -> None:
        super().__init__(minimum_access_level=2, **kwargs)

    @override
    async def _handle_running(self) -> None:
        pass
