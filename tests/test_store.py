# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
import os

import pytest

from bb import store


@pytest.fixture(params=['localfs'])
def any_store(request, tmp_path):
    """Contract fixture — every PhotoStore implementation must pass every test below."""
    if request.param == 'localfs':
        return store.LocalFSStore(str(tmp_path / 'photos'))


def test_put_get_exists_delete_round_trip(any_store):
    key = '1/7/1.jpg'
    assert any_store.exists(key) is False
    any_store.put(key, b'\xff\xd8bytes')
    assert any_store.exists(key) is True
    assert any_store.get(key) == b'\xff\xd8bytes'
    assert any_store.delete(key) is True
    assert any_store.exists(key) is False
    assert any_store.delete(key) is False


def test_get_missing_raises_keyerror(any_store):
    with pytest.raises(KeyError):
        any_store.get('1/7/9.jpg')


def test_put_overwrites(any_store):
    any_store.put('1/7/1.jpg', b'a')
    any_store.put('1/7/1.jpg', b'bb')
    assert any_store.get('1/7/1.jpg') == b'bb'


@pytest.mark.parametrize('bad', ['../x.jpg', '/1/7/1.jpg', '1/../7/1.jpg', '1\\7\\1.jpg', '', '1/7/'])
def test_bad_keys_rejected(any_store, bad):
    with pytest.raises(store.BadKey):
        any_store.put(bad, b'x')
    with pytest.raises(store.BadKey):
        any_store.exists(bad)


def test_localfs_writes_under_root_only(tmp_path):
    s = store.LocalFSStore(str(tmp_path / 'photos'))
    s.put('2/3/1.png', b'p')
    assert os.path.isfile(tmp_path / 'photos' / '2' / '3' / '1.png')


def test_from_paths_uses_photos_dir(env):
    s = store.from_paths()
    assert isinstance(s, store.LocalFSStore)
    assert s.root == env.photos_dir()


def test_failed_put_leaves_no_temp_file(tmp_path):
    s = store.LocalFSStore(str(tmp_path / 'photos'))
    with pytest.raises(TypeError):
        s.put('1/7/1.jpg', 'not bytes')  # str → f.write raises
    assert not s.exists('1/7/1.jpg')
    assert list((tmp_path / 'photos' / '1' / '7').glob('*')) == []
