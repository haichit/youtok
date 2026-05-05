class PipelineError(Exception):
    pass


class InsufficientDiskSpace(PipelineError):
    pass


class DownloadError(PipelineError):
    pass


class TranscribeError(PipelineError):
    pass


class SegmentError(PipelineError):
    pass


class RenderError(PipelineError):
    pass
