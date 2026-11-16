"""Real-time turn-exit and straight-loss audio training feedback package."""

from live_feedback.audio import (
    AudioCuePlayer,
    loss_to_frequency_hz,
    synthesize_chirp_pcm,
    synthesize_tone_pcm,
)
from live_feedback.engine import (
    BenchmarkStore,
    LiveFeedbackEngine,
    TurnBenchmark,
    TurnCompleteEvent,
    TurnExitEvent,
    estimate_straight_delta_s,
    project_to_centerline,
)
from live_feedback.stream_server import (
    Brt1IncrementalDecoder,
    ChunkedBrt1Handler,
    LiveStreamServer,
    compute_session_key,
)
from live_feedback.track_loader import (
    TrackData,
    TrackLoader,
    TurnDef,
    parse_track_payload,
    resolve_track_origin,
)

__all__ = [
    'AudioCuePlayer',
    'BenchmarkStore',
    'Brt1IncrementalDecoder',
    'ChunkedBrt1Handler',
    'LiveFeedbackEngine',
    'LiveStreamServer',
    'TrackData',
    'TrackLoader',
    'TurnBenchmark',
    'TurnCompleteEvent',
    'TurnDef',
    'TurnExitEvent',
    'compute_session_key',
    'estimate_straight_delta_s',
    'loss_to_frequency_hz',
    'parse_track_payload',
    'project_to_centerline',
    'resolve_track_origin',
    'synthesize_chirp_pcm',
    'synthesize_tone_pcm',
]
