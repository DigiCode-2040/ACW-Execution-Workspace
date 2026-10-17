"""Sort files in the Downloads folder into folders by file type."""
import argparse
from pathlib import Path
import shutil


EXTENSIONS = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp"},
    "Documents": {".pdf", ".docx", ".txt", ".xlsx", ".pptx"},
    "Installers": {".exe", ".msi"},
    "Archives": {".zip", ".rar", ".7z"},
}


def available_destination(folder: Path, filename: str) -> Path:
    destination = folder / filename
    if not destination.exists():
        return destination

    source = Path(filename)
    number = 1
    while True:
        destination = folder / f"{source.stem} ({number}){source.suffix}"
        if not destination.exists():
            return destination
        number += 1


def organize(target_folder: Path, dry_run: bool = False) -> int:
    if not target_folder.is_dir():
        raise NotADirectoryError(f"Target folder does not exist or is not a directory: {target_folder}")

    moved = 0
    for source in target_folder.iterdir():
        if not source.is_file():
            continue

        category = next(
            (name for name, extensions in EXTENSIONS.items() if source.suffix.lower() in extensions),
            None,
        )
        if category is None:
            continue

        category_folder = target_folder / category
        destination = available_destination(category_folder, source.name)
        if dry_run:
            print(f"Would move: {source.name} -> {category}/{destination.name}")
            moved += 1
            continue

        category_folder.mkdir(exist_ok=True)
        destination = available_destination(category_folder, source.name)
        shutil.move(str(source), str(destination))
        print(f"Moved: {source.name} -> {category}/{destination.name}")
        moved += 1

    action = "Would organize" if dry_run else "Organized"
    print(f"{action} {moved} file(s) in {target_folder}")
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=Path.home() / "Downloads",
        help="folder to organize (default: ~/Downloads)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list planned moves without moving files",
    )
    args = parser.parse_args()
    organize(args.target.expanduser().resolve(), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
