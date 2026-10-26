"""Recovery fixtures never call Docker, sudo, or touch a real VM."""
from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock,mock_open,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import admin


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'storage.tar'
    def tearDown(self):self.tmp.cleanup()
    def archive(self,extra=None):
        with tarfile.open(self.path,'w') as t:
            for name in ['storage/data.img','storage/windows_secure.vars','storage/windows_secure.rom','storage/windows_secure.tpm/tpm2-00.permall']:
                m=tarfile.TarInfo(name);m.size=4;t.addfile(m,io.BytesIO(b'data'))
            if extra:t.addfile(extra)
    def test_complete_archive_and_capacity(self):
        self.archive();result=admin.archive_info(self.path)
        self.assertEqual(result['entries'],4);self.assertEqual(result['requiredBytes'],16)
    def test_single_file_tpm_from_current_dockur_is_complete(self):
        with tarfile.open(self.path,'w') as t:
            for name in ['storage/data.img','storage/windows_secure.vars','storage/windows_secure.rom','storage/windows_secure.tpm']:
                m=tarfile.TarInfo(name);m.size=4;t.addfile(m,io.BytesIO(b'data'))
        self.assertEqual(admin.archive_info(self.path)['entries'],4)
    def test_unsafe_archive_members_rejected(self):
        for name,kind in [('storage/../../outside',tarfile.REGTYPE),('/etc/escape',tarfile.REGTYPE),('storage/link',tarfile.SYMTYPE),('storage/hard',tarfile.LNKTYPE),('storage/device',tarfile.CHRTYPE),('storage/fifo',tarfile.FIFOTYPE)]:
            with self.subTest(name=name):
                m=tarfile.TarInfo(name);m.type=kind;m.linkname='/etc'
                self.archive(m)
                with self.assertRaises(admin.Refused):admin.archive_info(self.path)
    def test_duplicate_archive_member_rejected(self):
        self.archive(tarfile.TarInfo('storage/data.img'))
        with self.assertRaises(admin.Refused):admin.archive_info(self.path)
    def test_incomplete_archive_rejected(self):
        with tarfile.open(self.path,'w'):pass
        with self.assertRaises(admin.Refused):admin.archive_info(self.path)
    def test_no_arbitrary_backup_path(self):
        for name in ('../backup','/etc','development-ready-20260101T000000Z/../other','storage.tar'):
            with self.assertRaises(admin.Refused):admin.checked_backup(self.path.parent,name)


class AllocationTests(unittest.TestCase):
    def test_capacity_limits(self):
        admin.validate_allocation(4,8,16,32*admin.GIB)
        for cpu,ram in ((True,8),(0,8),(17,8),(4,3),(4,25),(4,'8')):
            with self.assertRaises(admin.Refused):admin.validate_allocation(cpu,ram,16,32*admin.GIB)
    def test_allocation_preserves_every_other_setting(self):
        old='services:\n  windows:\n    environment:\n      CPU_CORES: "4"\n      RAM_SIZE: "8G"\n      BOOT_MODE: "windows_secure"\n      PORTS: "22"\n'
        changed=admin.replace_allocation(old,6,10)
        self.assertEqual(changed,old.replace('CPU_CORES: "4"','CPU_CORES: "6"').replace('RAM_SIZE: "8G"','RAM_SIZE: "10G"'))
        with self.assertRaises(admin.Refused):admin.replace_allocation(old+'      RAM_SIZE: "9G"\n',6,10)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.storage=self.root/'storage';self.storage.mkdir();(self.storage/'data.img').write_text('original')
        self.clone=self.root/'stage/storage';self.clone.mkdir(parents=True);(self.clone/'data.img').write_text('restored')
        self.p=admin.Provider.__new__(admin.Provider);self.p.storage=self.storage;self.p.anchor=self.root/'anchor';self.p.state={};self.p.save=Mock();self.p.down=Mock();self.p.up=Mock()
        self.p.uid=os.getuid();self.p.gid=os.getgid()
        self.p.verify=Mock(return_value=(self.root,{},10));self.p.extract=Mock(return_value=self.clone)
        self.name='development-ready-20261016T193430Z'
    def tearDown(self):self.tmp.cleanup()
    def run_restore(self,confirm=None,cmd=None):
        with patch('admin.trusted'),patch('admin.lifecycle_lock',return_value=nullcontext()),patch('admin.command',cmd or Mock()),patch('builtins.input',return_value=confirm or 'RESTORE '+self.name):
            return self.p.restore(self.name)
    def test_restore_preserves_old_disk_and_starts_after_mount(self):
        self.run_restore()
        old=Path(self.p.state['recovery']['previous'])
        self.assertEqual((old/'data.img').read_text(),'original')
        self.assertEqual((self.storage/'data.img').read_text(),'restored')
        self.p.up.assert_called_once();self.assertEqual(self.p.state['recovery']['status'],'restored')
    def test_cancellation_never_stops_original(self):
        with self.assertRaises(admin.Refused):self.run_restore('cancel')
        self.p.down.assert_not_called();self.assertEqual((self.storage/'data.img').read_text(),'original')
    def test_failed_mount_retains_both_versions_and_does_not_start(self):
        def fail(argv,*a,**kw):
            if argv[0]=='/usr/bin/mount':raise admin.Refused('fixture failure')
        with self.assertRaises(admin.Refused):self.run_restore(cmd=Mock(side_effect=fail))
        self.assertEqual(self.p.state['recovery']['status'],'needs-attention')
        self.assertEqual((Path(self.p.state['recovery']['previous'])/'data.img').read_text(),'original')
        self.p.up.assert_not_called()
    def test_rollback_returns_to_old_disk_and_keeps_new_copy(self):
        self.run_restore();self.p.up.reset_mock()
        with patch('admin.lifecycle_lock',return_value=nullcontext()),patch('admin.command'),patch('builtins.input',return_value='ROLLBACK WINDOWS'):
            self.p.rollback()
        self.assertEqual((self.storage/'data.img').read_text(),'original')
        self.assertEqual((Path(self.p.state['recovery']['restoredCopy'])/'data.img').read_text(),'restored')
        self.p.up.assert_called_once()
    def test_unrelated_rollback_path_refused(self):
        self.p.state['recovery']={'status':'restored','previous':'/some/other/storage-before-20260101T000000Z-abcdef'}
        with self.assertRaises(admin.Refused):self.p.rollback()
        self.p.down.assert_not_called()

class RecoveryRegressionTests(unittest.TestCase):
    setUp=RestoreTests.setUp
    tearDown=RestoreTests.tearDown
    run_restore=RestoreTests.run_restore
    def test_rollback_can_resume_after_storage_move_before_mount(self):
        self.run_restore()
        old=Path(self.p.state['recovery']['previous'])
        retained=self.storage.with_name('storage-after-20260101T000000Z-abcdef')
        self.storage.rename(retained);old.rename(self.storage)
        self.p.state['recovery'].update(status='rollback-switching',restoredCopy=str(retained))
        with patch('admin.lifecycle_lock',return_value=nullcontext()),patch('admin.command'),patch('builtins.input',return_value='ROLLBACK WINDOWS'):
            self.p.rollback()
        self.assertEqual((self.storage/'data.img').read_text(),'original')
        self.assertEqual((retained/'data.img').read_text(),'restored')
        self.assertEqual(self.p.state['recovery']['status'],'rolled-back')


class MountTests(unittest.TestCase):
    setUp=RestoreTests.setUp
    tearDown=RestoreTests.tearDown
    run_restore=RestoreTests.run_restore
    def test_mount_table_finds_same_filesystem_binds(self):
        table='36 25 0:32 / / rw - btrfs /dev/root rw\n90 36 0:32 /src /srv/anchor/storage rw - btrfs /dev/root rw\n91 36 0:32 /x /mnt/with\\040space rw - btrfs /dev/root rw\n'
        with patch('admin.open',mock_open(read_data=table),create=True):
            self.assertTrue(admin.mounted(Path('/srv/anchor/storage')))
            self.assertTrue(admin.mounted('/mnt/with space'))
            self.assertFalse(admin.mounted('/srv/anchor'))
        with patch('admin.open',side_effect=OSError,create=True),self.assertRaises(admin.Refused):admin.mounted('/srv')
    def rollback(self,layers,removable=True):
        self.run_restore();self.p.up.reset_mock()
        state={'layers':layers};calls=[]
        def cmd(argv,*a,**kw):
            calls.append(argv[0])
            if argv[0]=='/usr/bin/umount' and removable:state['layers']-=1
        with patch('admin.mounted',side_effect=lambda p:state['layers']>0),patch('admin.lifecycle_lock',return_value=nullcontext()),\
                patch('admin.command',Mock(side_effect=cmd)),patch('builtins.input',return_value='ROLLBACK WINDOWS'):
            self.p.rollback()
        return calls
    def test_rollback_unmounts_every_layer_before_switching(self):
        # os.path.ismount reports False for a same-filesystem bind; the mount table does not.
        with patch('admin.os.path.ismount',return_value=False):
            calls=self.rollback(2)
        self.assertEqual(calls,['/usr/bin/umount','/usr/bin/umount','/usr/bin/mount'])
        self.assertEqual((self.storage/'data.img').read_text(),'original');self.p.up.assert_called_once()
    def test_rollback_refuses_while_storage_stays_mounted(self):
        with self.assertRaisesRegex(admin.Refused,'still mounted'):self.rollback(1,removable=False)
        self.assertEqual((self.storage/'data.img').read_text(),'restored')
        self.assertEqual(self.p.state['recovery']['status'],'restored');self.p.up.assert_not_called()


class AllocationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.cpu=patch('admin.os.cpu_count',return_value=16);self.cpu.start();self.addCleanup(self.cpu.stop)
        self.memory=patch('admin.memory_info',return_value={'MemTotal':32*admin.GIB,'MemAvailable':16*admin.GIB});self.memory.start();self.addCleanup(self.memory.stop)

    def test_failed_new_start_restores_original_compose(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);compose=root/'compose.yml'
            original='services:\n  windows:\n    environment:\n      CPU_CORES: "4"\n      RAM_SIZE: "8G"\n      UNRELATED: "preserved"\n'
            compose.write_text(original)
            p=admin.Provider.__new__(admin.Provider);p.root=root;p.state={};p.save=Mock();p.down=Mock();p.running=Mock(return_value=True)
            p.service={'environment':{'CPU_CORES':'4','RAM_SIZE':'8G'}}
            p.up=Mock(side_effect=[admin.Refused('fixture new start failure'),None])
            with patch('admin.COMPOSE',compose),patch('admin.lifecycle_lock',return_value=nullcontext()),patch('admin.command'),patch('admin.os.chown'):
                with self.assertRaisesRegex(admin.Refused,'previous configuration restored'):p.allocate(5,8)
            self.assertEqual(compose.read_text(),original)
            self.assertEqual(p.up.call_count,2)
            self.assertEqual(p.state['allocation'],{'cpu':4,'ram':'8G'})
    def test_invalid_compose_never_stops_windows(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);compose=root/'compose.yml';compose.write_text('      CPU_CORES: "4"\n      RAM_SIZE: "8G"\n')
            p=admin.Provider.__new__(admin.Provider);p.root=root;p.service={'environment':{'RAM_SIZE':'8G'}};p.running=Mock(return_value=True);p.down=Mock()
            with patch('admin.COMPOSE',compose),patch('admin.command',side_effect=admin.Refused('invalid')),patch('admin.os.chown'):
                with self.assertRaises(admin.Refused):p.allocate(5,8)
            p.down.assert_not_called()


class SparseArchiveTests(unittest.TestCase):
    def test_actual_sparse_archive_roundtrip_and_tpm_requirement(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);storage=root/'storage';storage.mkdir()
            with (storage/'data.img').open('wb') as f:f.write(b'begin');f.seek(1024**3);f.write(b'end')
            for name in ('windows_secure.vars','windows_secure.rom'):(storage/name).write_text('firmware')
            archive=root/'test.tar'
            admin.command(['/usr/bin/tar','--sparse','-cpf',str(archive),'-C',str(root),'storage'])
            with self.assertRaisesRegex(admin.Refused,'TPM'):admin.archive_info(archive)
            (storage/'windows_secure.tpm').mkdir();(storage/'windows_secure.tpm/state').write_text('TPM')
            admin.command(['/usr/bin/tar','--sparse','-cpf',str(archive),'-C',str(root),'storage'])
            info=admin.archive_info(archive)
            self.assertLess(info['requiredBytes'],1024**2);self.assertGreater(info['logicalBytes'],1024**3)
            p=admin.Provider.__new__(admin.Provider);p.stage=Mock()
            with archive.open('rb') as f:clone=p.extract(Path('/proc/self/fd')/str(f.fileno()),root)
            with (clone/'data.img').open('rb') as f:
                self.assertEqual(f.read(5),b'begin');f.seek(1024**3);self.assertEqual(f.read(),b'end')
            self.assertEqual((clone/'windows_secure.tpm/state').read_text(),'TPM')
