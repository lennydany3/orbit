"""First-run model provisioning for the installed app."""
from orbit import config
from orbit.ipc import protocol
from orbit.voice import config as voice_config
from orbit.voice import provision


def test_shipped_models_are_copied_once_and_never_overwritten(tmp_path):
    source, target = tmp_path / "bundle", tmp_path / "models"
    (source / "openwakeword").mkdir(parents=True)
    (source / "silero_vad.onnx").write_bytes(b"new")
    (source / "openwakeword" / "hey_orbit.npz").write_bytes(b"wake")
    assert sorted(provision.copy_bundled(source, target)) == ["openwakeword/hey_orbit.npz", "silero_vad.onnx"]
    (target / "silero_vad.onnx").write_bytes(b"mine")                      # replaced by hand: left alone
    assert provision.copy_bundled(source, target) == []
    assert (target / "silero_vad.onnx").read_bytes() == b"mine"


def test_the_big_models_are_only_wanted_when_they_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_config, "WHISPER_DIR", tmp_path / "whisper")
    monkeypatch.setattr(voice_config, "PIPER_DIR", tmp_path / "piper")
    monkeypatch.setattr(voice_config, "PARAKEET_DIR", tmp_path / "parakeet")
    monkeypatch.setattr(voice_config, "STT_PROVIDER", "auto")
    kinds = [kind for kind, _ in provision.missing()]
    assert kinds == ["whisper", "piper", "parakeet"]
    (tmp_path / "whisper" / voice_config.WAKE_VERIFY_MODEL).mkdir(parents=True)
    (tmp_path / "whisper" / voice_config.WAKE_VERIFY_MODEL / "model.bin").write_bytes(b"x")
    (tmp_path / "piper").mkdir()
    (tmp_path / "piper" / f"{voice_config.PIPER_VOICE}.onnx").write_bytes(b"x")
    model = tmp_path / "parakeet" / voice_config.PARAKEET_MODEL
    model.mkdir(parents=True)
    for name in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"):
        (model / name).write_bytes(b"x")
    assert provision.missing() == []


def test_with_whisper_chosen_the_big_whisper_is_downloaded_instead(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_config, "WHISPER_DIR", tmp_path / "whisper")
    monkeypatch.setattr(voice_config, "PIPER_DIR", tmp_path / "piper")
    monkeypatch.setattr(voice_config, "PARAKEET_DIR", tmp_path / "parakeet")
    monkeypatch.setattr(voice_config, "STT_PROVIDER", "whisper")
    assert [name for _, name in provision.missing()] == [voice_config.WAKE_VERIFY_MODEL, voice_config.PIPER_VOICE, voice_config.WHISPER_MODEL]


def test_the_data_folder_is_the_project_when_run_from_source():
    assert config.DATA_DIR == config.PROJECT_ROOT or config.os.environ.get("ORBIT_DATA")
    assert not config.FROZEN


def test_a_notice_is_a_line_for_the_face():
    assert protocol.notice("Downloading") == {"type": "notice", "text": "Downloading"}
