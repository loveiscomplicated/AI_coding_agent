import pytest

@pytest.fixture
def egg_doneness():
    def _egg_doneness(seconds):
        if seconds < 2:
            return {'name': '날계란', 'emoji': '🥚💧'}
        elif seconds < 5:
            return {'name': '반반숙', 'emoji': '흐르는 느낌'}
        elif seconds < 10:
            return {'name': '반숙', 'emoji': '🟡🏆'}
        elif seconds < 20:
            return {'name': '완숙', 'emoji': '🟠'}
        else:
            return {'name': '터짐', 'emoji': '💥💀'}
    return _egg_doneness
