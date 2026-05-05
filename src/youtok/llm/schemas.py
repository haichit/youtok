from pydantic import BaseModel, Field


class SentenceRange(BaseModel):
    start: str = Field(pattern=r"^S\d{3}$")
    end: str = Field(pattern=r"^S\d{3}$")


class SubTopic(BaseModel):
    name: str
    start_sentence: str = Field(pattern=r"^S\d{3}$")
    end_sentence: str = Field(pattern=r"^S\d{3}$")
    parent: str | None = None
    children: list["SubTopic"] = []


class StageAOutput(BaseModel):
    main_topic: str
    intro_strip: SentenceRange | None = None
    outro_strip: SentenceRange | None = None
    sub_topics: list[SubTopic]


class StageBOutput(BaseModel):
    coherence_score: int = Field(ge=1, le=5)
    start_adjust: int = 0
    end_adjust: int = 0
    internal_break: SentenceRange | None = None
    notes: str = ""
