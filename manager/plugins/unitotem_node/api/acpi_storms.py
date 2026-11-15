from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from utils.models.user import UserPerms
from unitotem_node.system import acpi_storms


class AcpiStorms(WSAPIBase):
    @staticmethod
    @UserPerms.requires.none
    async def list():
        """The ACPI events the storm guard masked (the Info page reads it on load; changes are pushed)."""
        return WSBroadcast(storms=acpi_storms.read_storms())
