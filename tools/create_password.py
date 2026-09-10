# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import base64
import os

from opensmi.server.common import SECRET_KEY_NAME, get_scrypt_instance


def main() -> None:
    try:
        scrypt = get_scrypt_instance()
    except RuntimeError:
        print("Could not find valid pepper in environment!")
        new_pepper = base64.b64encode(os.urandom(32)).decode("ascii")
        print("New random pepper:", new_pepper)
        os.environ[SECRET_KEY_NAME] = new_pepper
        scrypt = get_scrypt_instance()

    while True:
        cleartext_password = input("Please enter a password: ")
        cleartext_password_again = input("Please re-enter the password: ")
        if cleartext_password == cleartext_password_again:
            print("Here is the password for use with the access control:")
            print(scrypt.derive(bytes(cleartext_password, "utf-8")))
            return
        print("The entered password do not match! Please try again.")


if __name__ == "__main__":
    main()
