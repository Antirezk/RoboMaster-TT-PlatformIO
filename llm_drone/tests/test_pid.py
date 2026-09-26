from control.pid_controller import PIDController
from control.rc_command import RCCommand


def test_pid_proportional_output_and_limit():
    pid = PIDController(kp=2, output_limit=10)
    assert pid.update(3, dt=0.1) == 6
    assert pid.update(100, dt=0.1) == 10


def test_pid_integral_limit_and_reset():
    pid = PIDController(kp=0, ki=1, integral_limit=2)
    assert pid.update(10, dt=1) == 2
    pid.reset()
    assert pid.integral == 0


def test_rc_command_clamps_all_channels():
    command = RCCommand(150, -130, 99.6, -100.6)
    assert command == RCCommand(100, -100, 100, -100)
    assert RCCommand.zero().is_zero
