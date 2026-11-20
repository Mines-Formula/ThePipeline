'''
NOTE: original code was adapted from the DBC repo in github.
This code was found to be unacceptably slow for this use case,
in the mean time, an untested, patch version has been created.

This code requires more testing and refactoring in the future.

@author Will Turchin

'''
import os


def deserialize(input_filepath: str, output_filepath: str) -> None:
     # Data file structure
    # String structure
    # 1B length + 127
    # xB data

    # CAN message structure
    # 1B length
    # 4B time (big endian)
    # 4B message id (big endian)
    # xB data
    if not os.path.exists(input_filepath):
        raise FileNotFoundError(f"{input_filepath} does not exist")

    with open(input_filepath, "rb") as file:
        input_data = file.read()

    output_parts = []
    i = 0
    data_size = len(input_data)

    while i < data_size:
        header_byte = input_data[i]
        i += 1
        
        if header_byte > 127:
            # String Mode
            length = header_byte - 127
            # Slice the entire string at once
            string_content = input_data[i : i + length].decode('ascii', errors='ignore')
            output_parts.append(string_content)
            i += length
        else:
            # CAN Message Mode: 4B Time, 4B ID, length bytes data
            length = header_byte
            # Use int.from_bytes on 4-byte slices
            time_val = int.from_bytes(input_data[i : i + 4], "big")
            msg_id = int.from_bytes(input_data[i + 4 : i + 8], "big")
            
            # Extract data bytes and convert to strings
            payload = input_data[i + 8 : i + 8 + length]
            payload_str = ",".join(map(str, payload))
            
            row = f"{time_val},{msg_id},{payload_str}"
            output_parts.append(row)
            i += 8 + length

        # Add newline after each entry (logic replacement for your trailing comma removal)
        output_parts.append("\n")

    # Write everything at once
    with open(output_filepath, "w") as file:
        # join() is O(n) and much faster than +=
        file.write("".join(output_parts).strip())

    # if not os.path.exists(input_filepath):
    #     raise FileNotFoundError("input_filepath does not exist")

    # with open(input_filepath, "rb") as file:
    #     input_data = file.read()
    # output_string = ""

    # # Data file structure

    # # String structure
    # # 1B length + 127
    # # xB data

    # # CAN message structure
    # # 1B length
    # # 4B time (big endian)
    # # 4B message id (big endian)
    # # xB data

    # data_length = 0
    # cur_data_index = 0
    # string_read_mode = False

    # print(len(input_data))
    # for i, byte in enumerate(input_data):
    #     if i % 50_000 == 0:
    #         print(f"Enumeration: {i}\n")
    #     # tranisiton to reading next line
    #     if cur_data_index == data_length:
    #         data_length = byte
    #         output_string = output_string[:-1]  # remove previous line's trailing comma
    #         output_string += "\n"
    #         if byte > 127:
    #             data_length -= 127
    #             string_read_mode = True
    #             cur_data_index = 0  # start directly at the string data
    #         else:
    #             string_read_mode = False
    #             cur_data_index = -8  # start by reading this line's metadata

    #     # read data in current line
    #     else:
    #         if string_read_mode:
    #             output_string += chr(byte)
    #         else:
    #             if (
    #                 cur_data_index < 0
    #             ):  # read message metadata (time and id, each 4 bytes)
    #                 relative_index = (cur_data_index + 1) % 4
    #                 if relative_index == 0:
    #                     number = int.from_bytes(input_data[i - 3 : i + 1])
    #                     output_string += str(number) + ","

    #             else:
    #                 output_string += str(byte) + ","
    #         cur_data_index += 1

    # print(f"{cur_data_index}\n{data_length}\n")
    # if cur_data_index != data_length:
    #     raise Exception("file corruption detected")

    # output_string = output_string[:-1]  # remove final trailing comma
    # with open(output_filepath, "w") as file:
    #     file.write(output_string)
