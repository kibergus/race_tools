import json
import os
from unittest.mock import MagicMock, patch
from strength_training.main import (
    TargetState,
    FanatecFFBController,
    StrengthTrainingApp,
    AudioFeedbackController,
    AppTrackingRunner,
    calculate_window_offset,
    get_descendant_pids,
    get_monitor_geometry,
    main,
    is_valid_target_angle,
    load_strength_config,
    save_strength_config,
)
from strength_training.screensaver_inhibitor import ScreensaverInhibitor


def test_target_state_initialization() -> None:
    target = TargetState()
    assert is_valid_target_angle(target.current_target_angle)
    assert target.cycle_step == 0


def test_target_state_update_bounds_and_deadzone() -> None:
    target = TargetState()
    curr_time = 100.0

    # Step through multiple updates (simulating 100 seconds of target shifts)
    for _ in range(100):
        curr_time += 1.0
        angle = target.update(curr_time)
        assert isinstance(angle, float)
        assert -90.0 <= angle <= 90.0
        # Interpolation between valid target positions could pass through, but end_angle must always be valid
        assert is_valid_target_angle(target.end_angle)
        assert is_valid_target_angle(target.start_angle)


def test_audio_controller_volume_curve() -> None:
    """Verify audio volume formula: 0% below 2 deg, 100% at/above 10 deg, linear in between."""
    # Under or equal to 2 degrees
    assert AudioFeedbackController.calculate_volume(0.0) == 0.0
    assert AudioFeedbackController.calculate_volume(1.0) == 0.0
    assert AudioFeedbackController.calculate_volume(2.0) == 0.0
    assert AudioFeedbackController.calculate_volume(-1.5) == 0.0
    assert AudioFeedbackController.calculate_volume(-2.0) == 0.0

    # Linear range 2.0 to 10.0 degrees
    assert AudioFeedbackController.calculate_volume(4.0) == 0.25
    assert AudioFeedbackController.calculate_volume(6.0) == 0.5
    assert AudioFeedbackController.calculate_volume(8.0) == 0.75
    assert AudioFeedbackController.calculate_volume(-6.0) == 0.5

    # At or above 10 degrees
    assert AudioFeedbackController.calculate_volume(10.0) == 1.0
    assert AudioFeedbackController.calculate_volume(15.0) == 1.0
    assert AudioFeedbackController.calculate_volume(90.0) == 1.0
    assert AudioFeedbackController.calculate_volume(-12.0) == 1.0


def test_audio_controller_mute_and_volume() -> None:
    """Verify AudioFeedbackController muting and volume updates."""
    with patch('strength_training.main.AudioFeedbackController.init_audio'):
        audio = AudioFeedbackController(muted=False)
        assert not audio.muted

        # When unmuted, volume follows calculation
        vol = audio.update_volume_for_deflection(6.0)
        assert vol == 0.5
        assert audio.current_volume == 0.5

        # Toggle mute
        muted = audio.toggle_mute()
        assert muted is True
        assert audio.muted is True
        assert audio.current_volume == 0.0

        # When muted, update_volume_for_deflection returns 0.0
        vol2 = audio.update_volume_for_deflection(10.0)
        assert vol2 == 0.0
        assert audio.current_volume == 0.0

        # Unmute
        audio.toggle_mute()
        assert audio.muted is False
        vol3 = audio.update_volume_for_deflection(10.0)
        assert vol3 == 1.0
        assert audio.current_volume == 1.0


def test_audio_mute_config_persistence(tmp_path) -> None:
    """Verify audio_muted setting is loaded and saved cleanly without overwriting other config keys."""
    cfg_file = str(tmp_path / '.brbr_strength.json')

    # Initial controller save strength
    c = FanatecFFBController(config_path=cfg_file)
    c.set_force_scale(0.8)

    # Save audio_muted
    save_strength_config({'audio_muted': True}, cfg_file)

    loaded = load_strength_config(cfg_file)
    assert loaded.get('strength') == 0.8
    assert loaded.get('audio_muted') is True

    # Update strength again - audio_muted should remain preserved
    c.set_force_scale(0.6)
    loaded_after = load_strength_config(cfg_file)
    assert loaded_after.get('strength') == 0.6
    assert loaded_after.get('audio_muted') is True


def test_force_scale_adjustment(tmp_path) -> None:
    cfg_file = str(tmp_path / 'test_strength.json')
    controller = FanatecFFBController(config_path=cfg_file)
    assert controller.force_scale == 1.0

    # Decrease force scale by 0.1
    controller.adjust_force_scale(-0.1)
    assert controller.force_scale == 0.9

    # Increase force scale by 0.1
    controller.adjust_force_scale(0.1)
    assert controller.force_scale == 1.0

    # Clamping tests
    controller.set_force_scale(1.5)
    assert controller.force_scale == 1.0

    controller.set_force_scale(-0.5)
    assert controller.force_scale == 0.0


def test_strength_persistence_load_and_save(tmp_path) -> None:
    cfg_file = str(tmp_path / '.brbr_strength.json')

    # 1. Non-existent config defaults to 1.0
    c1 = FanatecFFBController(config_path=cfg_file)
    assert c1.force_scale == 1.0

    # 2. Adjust and save strength
    c1.set_force_scale(0.7)
    assert os.path.exists(cfg_file)

    with open(cfg_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data.get('strength') == 0.7

    # 3. New controller loads persisted value
    c2 = FanatecFFBController(config_path=cfg_file)
    assert c2.force_scale == 0.7


def test_strength_persistence_corrupted_json(tmp_path) -> None:
    cfg_file = str(tmp_path / '.brbr_strength.json')
    with open(cfg_file, 'w', encoding='utf-8') as f:
        f.write('invalid json content')

    c = FanatecFFBController(config_path=cfg_file)
    assert c.force_scale == 1.0


def test_strength_persistence_percentage(tmp_path) -> None:
    cfg_file = str(tmp_path / '.brbr_strength.json')
    with open(cfg_file, 'w', encoding='utf-8') as f:
        json.dump({'strength': 80}, f)

    c = FanatecFFBController(config_path=cfg_file)
    assert c.force_scale == 0.8


@patch('strength_training.main.tk.Tk')
def test_app_window_initialization(mock_tk_class: MagicMock, tmp_path) -> None:
    mock_root = MagicMock()
    mock_tk_class.return_value = mock_root

    cfg_file = str(tmp_path / 'test_strength.json')
    controller = FanatecFFBController(config_path=cfg_file)

    with patch('strength_training.main.StrengthTrainingApp.build_gui'), \
         patch('strength_training.main.StrengthTrainingApp.update_gui'), \
         patch('strength_training.main.AudioFeedbackController.init_audio'):
        app = StrengthTrainingApp(controller)

        # Verify window management configuration
        mock_root.attributes.assert_called_with('-fullscreen', True)
        mock_root.focus_force.assert_called_once()

        # Verify bind_all calls for key shortcuts
        bound_events = [call[0][0] for call in mock_root.bind_all.call_args_list]
        assert '<Escape>' in bound_events
        assert 'q' in bound_events
        assert 'Q' in bound_events
        assert '[' in bound_events
        assert ']' in bound_events
        assert 'r' in bound_events
        assert 'R' in bound_events
        assert 'v' in bound_events
        assert 'V' in bound_events
        assert 's' in bound_events
        assert 'S' in bound_events
        assert 'm' in bound_events
        assert 'M' in bound_events
        assert hasattr(app, 'session_start_time')
        assert hasattr(app, 'total_error')
        assert hasattr(app, 'avg_error')
        assert hasattr(app, 'inhibitor')
        assert hasattr(app, 'visual_mode')
        assert hasattr(app, 'audio_controller')
        assert app.visual_mode == 'cat_arrow'
        assert app.inhibitor.active


@patch('strength_training.main.tk.Tk')
def test_app_toggle_audio_mute(mock_tk_class: MagicMock, tmp_path) -> None:
    mock_root = MagicMock()
    mock_tk_class.return_value = mock_root

    cfg_file = str(tmp_path / 'test_strength.json')
    controller = FanatecFFBController(config_path=cfg_file)

    with patch('strength_training.main.StrengthTrainingApp.build_gui'), \
         patch('strength_training.main.StrengthTrainingApp.update_gui'), \
         patch('strength_training.main.AudioFeedbackController.init_audio'):
        app = StrengthTrainingApp(controller)
        assert app.audio_muted is False

        # Toggle mute on
        app.toggle_audio_mute()
        assert app.audio_muted is True
        saved = load_strength_config(cfg_file)
        assert saved.get('audio_muted') is True

        # Toggle mute off
        app.toggle_audio_mute()
        assert app.audio_muted is False
        saved2 = load_strength_config(cfg_file)
        assert saved2.get('audio_muted') is False


@patch('strength_training.main.tk.Tk')
def test_visual_mode_toggling_and_cat_mode_rendering(mock_tk_class: MagicMock, tmp_path) -> None:
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    mock_tk_class.return_value = mock_root

    cfg_file = str(tmp_path / 'test_strength.json')
    controller = FanatecFFBController(config_path=cfg_file)

    with patch('strength_training.main.tk.Canvas', return_value=mock_canvas), \
         patch('strength_training.main.StrengthTrainingApp.update_gui'):
        app = StrengthTrainingApp(controller)

        # Initial mode should be cat_arrow (default)
        assert app.visual_mode == 'cat_arrow'

        # Test cat arrow coordinate calculation
        app._update_cat_arrow_coords(wx=200.0, wy=200.0, wheel_angle=0.0)

        # Toggle to regular mode
        app.toggle_visual_mode()
        assert app.visual_mode == 'regular'

        # Toggle to full cat mode
        app.toggle_visual_mode()
        assert app.visual_mode == 'cat'

        # Test cat coordinate calculation when target is far
        app._update_cat_coords(wheel_angle=0.0, target_angle=45.0, current_time=100.0)

        # Test cat coordinate calculation when target is nearby (catching mode)
        app._update_cat_coords(wheel_angle=10.0, target_angle=12.0, current_time=100.0)

        # Toggle back to cat_arrow mode
        app.toggle_visual_mode()
        assert app.visual_mode == 'cat_arrow'


@patch('strength_training.main.tk.Tk')
def test_app_average_deviation_and_reset(mock_tk_class: MagicMock, tmp_path) -> None:
    mock_root = MagicMock()
    mock_tk_class.return_value = mock_root

    cfg_file = str(tmp_path / 'test_strength.json')
    controller = FanatecFFBController(config_path=cfg_file)

    with patch('strength_training.main.StrengthTrainingApp.build_gui'), \
         patch('strength_training.main.StrengthTrainingApp.update_gui'):
        app = StrengthTrainingApp(controller)

        # Simulate frame updates accumulating error
        app.total_error += 10.0
        app.error_samples += 2
        app.avg_error = app.total_error / app.error_samples
        assert app.avg_error == 5.0

        # Reset stats
        app.reset_stats()
        assert app.total_error == 0.0
        assert app.error_samples == 0
        assert app.avg_error == 0.0


def test_screensaver_inhibitor_start_and_stop() -> None:
    inhibitor = ScreensaverInhibitor()
    assert not inhibitor.active

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='uint32 12345\n')
        inhibitor.start(window_id=0x1234)

        assert inhibitor.active
        assert inhibitor.window_id == 0x1234

        # Check DBus / xdg-screensaver calls
        assert mock_run.called

        # Stop inhibitor
        inhibitor.stop()
        assert not inhibitor.active
        assert inhibitor.dbus_cookie is None
        assert inhibitor.window_id is None

        # Test win_id alias
        inhibitor.start(win_id=0x5678)
        assert inhibitor.active
        assert inhibitor.window_id == 0x5678
        inhibitor.stop()


@patch('strength_training.main.tk.Tk')
def test_gui_header_labels_position(mock_tk_class: MagicMock, tmp_path) -> None:
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    mock_tk_class.return_value = mock_root

    cfg_file = str(tmp_path / 'test_strength.json')
    controller = FanatecFFBController(config_path=cfg_file)

    with patch('strength_training.main.tk.Canvas', return_value=mock_canvas), \
         patch('strength_training.main.StrengthTrainingApp.update_gui'):
        app = StrengthTrainingApp(controller)

        # Retrieve text creation calls from Canvas
        text_calls = mock_canvas.create_text.call_args_list
        y_coords = {call[1].get('text', ''): call[0][1] for call in text_calls if len(call[0]) > 1}

        assert 'WORKOUT TIME: 00:00' in y_coords
        assert 'FIGHT THE CENTERING SPRING TO MATCH THE TARGET INDICATOR' in y_coords

        # Verify WORKOUT TIME is higher than FIGHT THE... label
        # Higher position in Tkinter canvas means smaller Y coordinate value
        assert y_coords['WORKOUT TIME: 00:00'] < y_coords['FIGHT THE CENTERING SPRING TO MATCH THE TARGET INDICATOR']
        assert y_coords['WORKOUT TIME: 00:00'] == app.header_y
        assert y_coords['FIGHT THE CENTERING SPRING TO MATCH THE TARGET INDICATOR'] == app.subtitle_y


@patch('strength_training.main.screeninfo.get_monitors')
@patch('strength_training.main.tk.Tk')
def test_switch_screen(mock_tk_class: MagicMock, mock_get_monitors: MagicMock, tmp_path) -> None:
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    mock_tk_class.return_value = mock_root

    mon0 = MagicMock(width=1920, height=1080, x=0, y=0)
    mon1 = MagicMock(width=2560, height=1440, x=1920, y=0)
    mock_get_monitors.return_value = [mon0, mon1]

    cfg_file = str(tmp_path / 'test_strength.json')
    controller = FanatecFFBController(config_path=cfg_file)

    with patch('strength_training.main.tk.Canvas', return_value=mock_canvas), \
         patch('strength_training.main.StrengthTrainingApp.update_gui'):
        app = StrengthTrainingApp(controller)
        assert app.current_monitor_index == 0
        assert app.width == 1920
        assert app.height == 1080

        # Call switch_screen
        app.switch_screen()

        assert app.current_monitor_index == 1
        assert app.width == 2560
        assert app.height == 1440
        assert app.x_offset == 1920
        assert app.y_offset == 0
        mock_root.geometry.assert_called_with('2560x1440+1920+0')
        assert load_strength_config(cfg_file).get('screen') == 1

        # Call switch_screen again (cycling back to monitor 0)
        app.switch_screen()
        assert app.current_monitor_index == 0
        assert app.width == 1920
        assert app.height == 1080
        mock_root.geometry.assert_called_with('1920x1080+0+0')
        assert load_strength_config(cfg_file).get('screen') == 0


def test_calculate_window_offset() -> None:
    """Verify inverted offset scaling so that at 90 deg deflection, exactly 1/4 window is left on screen."""
    screen_w = 1920

    # 0 deg deflection -> 0 offset (perfectly centered)
    assert calculate_window_offset(screen_w, 0.0) == 0

    # +90 deg deflection -> -0.75 * 1920 = -1440 offset (1/4 of window, 480px, remains on screen)
    offset_pos_90 = calculate_window_offset(screen_w, 90.0)
    assert offset_pos_90 == -1440
    assert (screen_w - abs(offset_pos_90)) == screen_w / 4

    # -90 deg deflection -> +1440 offset (1/4 of window remains on screen)
    offset_neg_90 = calculate_window_offset(screen_w, -90.0)
    assert offset_neg_90 == 1440
    assert (screen_w - offset_neg_90) == screen_w / 4

    # Linear interpolation at 45 deg -> -720 offset
    assert calculate_window_offset(screen_w, 45.0) == -720
    assert calculate_window_offset(screen_w, -45.0) == 720

    # Clamping beyond 90 deg
    assert calculate_window_offset(screen_w, 120.0) == -1440
    assert calculate_window_offset(screen_w, -120.0) == 1440


def test_controller_get_current_angle() -> None:
    """Verify FanatecFFBController.get_current_angle calculations."""
    controller = FanatecFFBController()
    assert controller.get_current_angle() == 0.0

    # Mock connected device
    controller.dev = MagicMock()
    controller.abs_min = 0
    controller.abs_max = 65535
    controller.hw_range_deg = 360.0

    # Center position
    controller.raw_position = 32767.5
    assert abs(controller.get_current_angle()) < 0.01

    # 90 degrees right (half range is 32767.5, max angle is 180, so 90 deg is 32767.5 + 32767.5/2)
    controller.raw_position = 32767.5 + (32767.5 / 2.0)
    assert abs(controller.get_current_angle() - 90.0) < 0.1

    # 90 degrees left
    controller.raw_position = 32767.5 - (32767.5 / 2.0)
    assert abs(controller.get_current_angle() - (-90.0)) < 0.1


def test_get_monitor_geometry() -> None:
    """Verify get_monitor_geometry helper returns correct monitor coordinates."""
    mon0 = MagicMock(width=1920, height=1080, x=0, y=0)
    mon1 = MagicMock(width=2560, height=1440, x=1920, y=100)

    with patch('strength_training.main.screeninfo.get_monitors', return_value=[mon0, mon1]):
        assert get_monitor_geometry(0) == (0, 0, 1920, 1080)
        assert get_monitor_geometry(1) == (1920, 100, 2560, 1440)
        assert get_monitor_geometry(5) == (1920, 100, 2560, 1440)  # Clamped to last monitor

    with patch('strength_training.main.screeninfo.get_monitors', side_effect=Exception('Failed')):
        assert get_monitor_geometry(0) == (0, 0, 1920, 1080)


def test_get_descendant_pids() -> None:
    """Verify recursive descendant PID extraction from proc."""
    current_pid = os.getpid()
    pids = get_descendant_pids(current_pid)
    assert current_pid in pids


def test_app_tracking_runner_initialization_and_methods(tmp_path) -> None:
    """Verify AppTrackingRunner setup, window finding, moving, and shutdown."""
    cfg_file = str(tmp_path / 'test_strength.json')
    controller = FanatecFFBController(config_path=cfg_file)

    with patch('strength_training.main.AudioFeedbackController.init_audio'), \
         patch('strength_training.main.get_monitor_geometry', return_value=(0, 0, 1920, 1080)):
        runner = AppTrackingRunner(controller, command='mpv test.mp4', monitor_index=0)
        assert runner.screen_width == 1920
        assert runner.screen_height == 1080
        assert runner.command == 'mpv test.mp4'

        # Test launch_app
        with patch('subprocess.Popen') as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            assert runner.launch_app() is True
            assert runner.process == mock_proc
            assert mock_popen.called
            # Verify X11 env variables injected
            call_kwargs = mock_popen.call_args[1]
            assert call_kwargs['env']['WAYLAND_DISPLAY'] == ''
            assert call_kwargs['env']['QT_QPA_PLATFORM'] == 'xcb'

            # Test find_window with xdotool
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout='123456\n')
                wid = runner.find_window(timeout=0.5)
                assert wid == 123456

            # Test move_resize_window fallback to subprocess
            with patch('subprocess.run') as mock_run:
                runner.move_resize_window(123456, 100, 50, 1920, 1080)
                assert mock_run.called

            # Test stop
            runner.stop()
            assert not runner.running
            assert mock_proc.terminate.called


def test_cli_main_dispatch(tmp_path) -> None:
    """Verify main() CLI dispatches to AppTrackingRunner when --app is given, else StrengthTrainingApp."""
    cfg_file = str(tmp_path / 'test_strength.json')

    with patch('strength_training.main.FanatecFFBController') as mock_controller_cls, \
         patch('strength_training.main.AppTrackingRunner') as mock_runner_cls, \
         patch('strength_training.main.StrengthTrainingApp') as mock_gui_cls, \
         patch('strength_training.main.save_strength_config') as mock_save_cfg:

        mock_ctrl = MagicMock()
        mock_ctrl.find_devices.return_value = False
        mock_ctrl.config_path = cfg_file
        mock_controller_cls.return_value = mock_ctrl

        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        mock_gui = MagicMock()
        mock_gui_cls.return_value = mock_gui

        # Case 1: with --app and --screen
        main(['--app', 'mpv my_video.mp4', '--screen', '1'])
        mock_save_cfg.assert_called_with({'screen': 1}, cfg_file)
        mock_runner_cls.assert_called_once_with(mock_ctrl, 'mpv my_video.mp4', monitor_index=1)
        mock_runner.run.assert_called_once()
        mock_runner.stop.assert_called_once()

        mock_runner_cls.reset_mock()
        mock_runner.reset_mock()
        mock_save_cfg.reset_mock()

        # Case 2: without --app (standard UI, default screen None)
        main([])
        mock_gui_cls.assert_called_once_with(mock_ctrl, monitor_index=None)
        mock_gui.run.assert_called_once()


def test_screen_persistence_app_and_runner(tmp_path) -> None:
    """Verify screen selection is remembered across sessions and applies to both GUI and AppTrackingRunner."""
    cfg_file = str(tmp_path / '.brbr_strength.json')
    controller = FanatecFFBController(config_path=cfg_file)

    mon0 = MagicMock(width=1920, height=1080, x=0, y=0)
    mon1 = MagicMock(width=2560, height=1440, x=1920, y=0)

    with patch('strength_training.main.screeninfo.get_monitors', return_value=[mon0, mon1]), \
         patch('strength_training.main.tk.Tk'), \
         patch('strength_training.main.tk.Canvas'), \
         patch('strength_training.main.StrengthTrainingApp.update_gui'), \
         patch('strength_training.main.AudioFeedbackController.init_audio'):

        # 1. Initial GUI launch defaults to monitor 0
        app1 = StrengthTrainingApp(controller)
        assert app1.current_monitor_index == 0

        # Switch to monitor 1 in GUI
        app1.switch_screen()
        assert app1.current_monitor_index == 1
        assert load_strength_config(cfg_file).get('screen') == 1

        # 2. Next GUI launch without monitor_index should remember screen 1
        app2 = StrengthTrainingApp(controller)
        assert app2.current_monitor_index == 1
        assert app2.width == 2560
        assert app2.height == 1440
        assert app2.x_offset == 1920

        # 3. External App tracking runner launched without monitor_index should also use saved screen 1
        runner = AppTrackingRunner(controller, command='mpv test.mp4')
        assert runner.monitor_index == 1
        assert runner.screen_width == 2560
        assert runner.screen_height == 1440
        assert runner.screen_x == 1920
        assert runner.screen_y == 0

        # 4. Explicit override still works
        runner_explicit = AppTrackingRunner(controller, command='mpv test.mp4', monitor_index=0)
        assert runner_explicit.monitor_index == 0
        assert runner_explicit.screen_width == 1920
        assert runner_explicit.screen_height == 1080
        assert runner_explicit.screen_x == 0


