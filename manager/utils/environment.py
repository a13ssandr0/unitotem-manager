from os import urandom
from typing import Any, Self

from loguru import logger
from pydantic import Field, ModelWrapValidatorHandler, PrivateAttr, ValidationError, model_validator
from pydantic_settings import BaseSettings

from utils.models.command_line import cmdargs


# noinspection PyDataclass
class Environment(BaseSettings, env_file=cmdargs.envfile, frozen=True):
    # model_config = ConfigDict()
    auth_token: str = Field(default_factory=lambda: urandom(24).hex())
    instance_id: str = Field(default_factory=lambda: urandom(16).hex())
    hotspot_uuid: str = Field(default_factory=lambda: urandom(16).hex())

    _unitotem_first_boot: bool = PrivateAttr(True)

    # noinspection PyNestedDecorators
    @model_validator(mode='wrap')
    @classmethod
    def validate(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            self = handler(data)
            if missing := set(cls.model_fields.keys()) - set(data.keys()):
                # if any key defined in model fields is not present in data, save the env file
                logger.debug('Saving missing environment variables ({}) in {}', ', '.join(missing),
                             cls.model_config['env_file'])
                self.save()
            return self
        except ValidationError:
            logger.error('Model {} failed to validate with data {}', cls, data)
            raise

    def save(self):
        with open(self.model_config['env_file'], 'w') as file:
            for k, v in self.model_dump().items():
                file.write(f'{k.upper()}={v}\n')

#TODO replace all os.environ occurrencies
environ = Environment()
