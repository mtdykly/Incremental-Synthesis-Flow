import subprocess


def get_modified_files(
        base_commit,
        new_commit,
        repo
):

    cmd = [
        "git",
        "-C",
        repo,
        "diff",
        "--name-only",
        base_commit,
        new_commit
    ]


    result = subprocess.check_output(
        cmd,
        text=True
    )


    files = [
        x.strip()
        for x in result.splitlines()
        if x.strip()
    ]


    return files