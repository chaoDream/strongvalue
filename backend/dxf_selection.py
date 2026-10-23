"""Strict raw-object references shared by fragment and assembly requests."""
from typing import Annotated
from pydantic import BaseModel,ConfigDict,Field,StringConstraints,model_validator

Hash=Annotated[str,StringConstraints(strict=True,pattern=r'^[a-f0-9]{64}$')]


class SourcePart(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate_id:Annotated[str,StringConstraints(strict=True,pattern=r'^[a-f0-9]{32}$')]
    object_hash:Hash
    reverse:bool=Field(strict=True)


class FragmentIn(SourcePart):
    from_point:int=Field(strict=True,ge=1,le=101)
    to_point:int=Field(strict=True,ge=1,le=101)

    @model_validator(mode='after')
    def ordered(self):
        if self.from_point>=self.to_point:raise ValueError('原始顶点范围须从小到大；改变方向请明确选择反向')
        return self


AssemblyPart=SourcePart|FragmentIn
