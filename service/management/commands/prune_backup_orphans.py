"""Remove only unreferenced encrypted/temp blobs older than a day."""
import time
from django.conf import settings
from django.core.management.base import BaseCommand
from service.models import Backup


class Command(BaseCommand):
    help = "Clean old unreferenced backup files (dry-run unless --apply)."
    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
    def handle(self, *args, **options):
        referenced = {(str(b.school_id), b.blob_name) for b in Backup.objects.all().iterator()}
        count = 0
        for directory in settings.BACKUP_ROOT.glob("*"):
            if not directory.is_dir() or directory.is_symlink():
                continue
            for path in directory.iterdir():
                if path.is_symlink() or not path.is_file() or path.stat().st_mtime > time.time() - 86400:
                    continue
                if (directory.name, path.name) not in referenced and (path.name.endswith(".enc") or path.name.startswith(".upload-")):
                    count += 1
                    if options["apply"]:
                        path.unlink()
        self.stdout.write(f"{count} old orphan files; apply={options['apply']}")
