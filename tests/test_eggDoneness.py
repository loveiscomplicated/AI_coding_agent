import pytest
from eggDoneness import egg_doneness


def test_egg_doneness_0_seconds():
    assert egg_doneness(0) == {'name': '날계란', 'emoji': '🥚💧'}


def test_egg_doneness_3_seconds():
    assert egg_doneness(3) == {'name': '반반숙', 'emoji': '흐르는 느낌'}


def test_egg_doneness_6_seconds():
    assert egg_doneness(6) == {'name': '반숙', 'emoji': '🟡🏆'}


def test_egg_doneness_15_seconds():
    assert egg_doneness(15) == {'name': '터짐', 'emoji': '💥💀'}
