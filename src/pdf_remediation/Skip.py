from .utilities.Resources import get_project_path
import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Add a file to the skipped files list."
    )
    parser.add_argument("project_name", help="Project directory name.")
    parser.add_argument(
        "file_to_skip",
        type=str,
        help="Relative path of the file to skip."
    )
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print()
        
        source_path = get_project_path(args.project_name)
        # Open the skipped files list from a text file
        skipped_files_path = source_path / "skipped_files.txt"
        skipped_files_path.parent.mkdir(parents=True, exist_ok=True)
        skipped_files = []
        if skipped_files_path.exists():
            with open(skipped_files_path, 'r') as f:
                for line in f:
                    skipped_file = line.strip()
                    if skipped_file and skipped_file not in skipped_files:
                        skipped_files.append(skipped_file)

        if args.file_to_skip not in skipped_files:
            skipped_files.append(args.file_to_skip)
            with open(skipped_files_path, 'w') as f:
                for skipped_file in skipped_files:
                    f.write(f"{skipped_file}\n")
            print(f"Added to skipped files: {args.file_to_skip}")
        else:
            print(f"File already in skipped files: {args.file_to_skip}")
