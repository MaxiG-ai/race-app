from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Seconds = Annotated[int, Field(ge=0)]
Metres = Annotated[float, Field(ge=0)]


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Patch(BaseModel):
    """Write bodies: a field left out is left alone, an explicit null clears it. Read
    them with `model_dump(exclude_unset=True)`."""

    model_config = ConfigDict(extra="forbid")
