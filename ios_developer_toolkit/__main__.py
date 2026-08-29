import sys

from ios_developer_toolkit.entrypoint import dispatch_internal


def main() -> int:
    internal_result = dispatch_internal(sys.argv[1:])
    if internal_result is not None:
        return internal_result
    from ios_developer_toolkit.app import main as application_main

    return application_main()


if __name__ == "__main__":
    raise SystemExit(main())
