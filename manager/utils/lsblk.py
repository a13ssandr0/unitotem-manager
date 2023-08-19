__all__ = ["lsblk"]



from json import loads
from subprocess import PIPE, run
from typing import ForwardRef, Optional

from pydantic import BaseModel, Field, validator

# class Blk has an attribute (children) of the same type of the class itself
# since in python class are not yet defined during creation, they cannot reference
# themselves, so we need a placeholder that has to be updated once the class is created
Blk = ForwardRef('Blk') # type: ignore

class Blk(BaseModel):
    name:str                     = Field(description='device name')
    rm:bool                      = Field(description='removable device')
    size:int                     = Field(description='size of the device')
    ro:bool                      = Field(description='read-only device')
    type:str                     = Field(description='device type')
    hotplug:bool                 = Field(description='removable or hotplug device (usb, pcmcia, ...)')
    mountpoint:Optional[str]     = Field(description='where the device is mounted')
    fssize:Optional[int]         = Field(description='filesystem size')
    fstype:Optional[str]         = Field(description='filesystem type')
    fsused:Optional[int]         = Field(description='filesystem size used')
    fsuse_perc:Optional[int]     = Field(alias='fsuse%', description='filesystem use percentage')
    fsavail:Optional[int]        = Field(description='filesystem size available')
    children:Optional[list[Blk]] = []

    @validator('fsuse_perc', pre=True)
    def validate_percentage(cls, v:str):
        if not v:
            return None
        return int(v.split('%')[0])

Blk.model_rebuild()




def lsblk():
    return [Blk.model_validate(dev) for dev in loads(run([
            '/usr/bin/lsblk', '-JMbe7',
            '-oNAME,RM,SIZE,RO,TYPE,HOTPLUG,MOUNTPOINT,FSSIZE,FSTYPE,FSUSED,FSUSE%,FSAVAIL'
        ], stdout=PIPE).stdout.decode())['blockdevices']]





# if __name__ == '__main__':
#     from subprocess import run, PIPE
#     from json import loads, dumps
#     # from utils.lsblk import Blk
#     blks = []
#     for dev in loads(run(['/usr/bin/lsblk', '-JMbe7', '-oNAME,RM,SIZE,RO,TYPE,HOTPLUG,MOUNTPOINT,FSSIZE,FSTYPE,FSUSED,FSUSE%,FSAVAIL'], stdout=PIPE).stdout.decode())['blockdevices']:
#         b= Blk.parse_obj(dev)
#         blks.append(b)
#     print(dumps(blks, indent=4))
