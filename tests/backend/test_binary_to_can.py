import pytest

from binary_to_can.deserializer import deserialize


def test_deserialize_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        deserialize(str(tmp_path / "nonexistent.bin"), str(tmp_path / "out.txt"))


def test_deserialize_can_message(tmp_path):
    f_in = tmp_path / "data.bin"
    f_out = tmp_path / "out.txt"
    time_val, msg_id = 1234, 567
    payload = bytes([0xAB, 0xCD])
    packet = bytes([2]) + time_val.to_bytes(4, "big") + msg_id.to_bytes(4, "big") + payload
    f_in.write_bytes(packet)
    deserialize(str(f_in), str(f_out))
    result = f_out.read_text().strip()
    assert result == f"{time_val},{msg_id},{0xAB},{0xCD}"


def test_deserialize_string_mode(tmp_path):
    f_in = tmp_path / "data.bin"
    f_out = tmp_path / "out.txt"
    message = "hello"
    header = 127 + len(message)
    packet = bytes([header]) + message.encode("ascii")
    f_in.write_bytes(packet)
    deserialize(str(f_in), str(f_out))
    result = f_out.read_text().strip()
    assert result == message


def test_deserialize_mixed_entries(tmp_path):
    f_in = tmp_path / "data.bin"
    f_out = tmp_path / "out.txt"
    msg = "hdr"
    str_packet = bytes([127 + len(msg)]) + msg.encode("ascii")
    can_packet = bytes([1]) + (100).to_bytes(4, "big") + (200).to_bytes(4, "big") + bytes([15])
    f_in.write_bytes(str_packet + can_packet)
    deserialize(str(f_in), str(f_out))
    lines = f_out.read_text().strip().splitlines()
    assert lines[0] == "hdr"
    assert lines[1] == "100,200,15"


def test_deserialize_multiple_can_messages(tmp_path):
    f_in = tmp_path / "data.bin"
    f_out = tmp_path / "out.txt"

    def make_can(time_val, msg_id, data):
        return bytes([len(data)]) + time_val.to_bytes(4, "big") + msg_id.to_bytes(4, "big") + bytes(data)

    f_in.write_bytes(make_can(1000, 10, [1, 2]) + make_can(2000, 20, [3]))
    deserialize(str(f_in), str(f_out))
    lines = f_out.read_text().strip().splitlines()
    assert lines[0] == "1000,10,1,2"
    assert lines[1] == "2000,20,3"


def test_deserialize_creates_output_file(tmp_path):
    f_in = tmp_path / "data.bin"
    f_out = tmp_path / "out.txt"
    f_in.write_bytes(bytes([1]) + (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + bytes([0]))
    deserialize(str(f_in), str(f_out))
    assert f_out.exists()
