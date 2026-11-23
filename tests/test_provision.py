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
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    kinds = [kind for kind, _ in provision.missing()]
    assert kinds == ["whisper", "piper", "whisper"]
    for sub in (f"whisper/{voice_config.WAKE_VERIFY_MODEL}", f"whisper/{voice_config.WHISPER_MODEL}"):
        (tmp_path / sub).mkdir(parents=True)
        (tmp_path / sub / "model.bin").write_bytes(b"x")
    (tmp_path / "piper").mkdir()
    (tmp_path / "piper" / f"{voice_config.PIPER_VOICE}.onnx").write_bytes(b"x")
    assert provision.missing() == []


def test_with_a_sarvam_key_the_local_speech_model_is_not_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_config, "WHISPER_DIR", tmp_path / "whisper")
    monkeypatch.setattr(voice_config, "PIPER_DIR", tmp_path / "piper")
    monkeypatch.setenv("SARVAM_API_KEY", "k" * 12)
    monkeypatch.setattr(voice_config, "STT_PROVIDER", "auto")
    assert [name for _, name in provision.missing()] == [voice_config.WAKE_VERIFY_MODEL, voice_config.PIPER_VOICE]


def test_the_data_folder_is_the_project_when_run_from_source():
    assert config.DATA_DIR == config.PROJECT_ROOT or config.os.environ.get("ORBIT_DATA")
    assert not config.FROZEN


def test_a_notice_is_a_line_for_the_face():
    assert protocol.notice("Downloading") == {"type": "notice", "text": "Downloading"}
