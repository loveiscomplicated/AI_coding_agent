import pytest

webdriver = pytest.importorskip("selenium.webdriver")
Service = pytest.importorskip("selenium.webdriver.chrome.service").Service
ChromeDriverManager = pytest.importorskip("webdriver_manager.chrome").ChromeDriverManager


@pytest.fixture
def browser():
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    yield driver
    driver.quit()


def test_index_html_opens(browser):
    browser.get('file://{}/index.html'.format('/tmp/agent_workspaces/task-001_1775022740'))
    assert "index.html" in browser.title


def test_emoji_displayed(browser):
    browser.get('file://{}/index.html'.format('/tmp/agent_workspaces/task-001_1775022740'))
    assert '🔥' in browser.page_source
    assert '🍳' in browser.page_source
    assert '💧' in browser.page_source
    assert '🥚' in browser.page_source


def test_pot_and_egg_visual_separation(browser):
    browser.get('file://{}/index.html'.format('/tmp/agent_workspaces/task-001_1775022740'))
    pot_element = browser.find_element('xpath', "//div[contains(text(),'🍳')]" )
    egg_element = browser.find_element('xpath', "//div[contains(text(),'🥚')]" )
    assert pot_element.location['y'] < egg_element.location['y']  # 냄비가 계란 위에 있어야 함


def test_egg_cursor_style(browser):
    browser.get('file://{}/index.html'.format('/tmp/agent_workspaces/task-001_1775022740'))
    egg_element = browser.find_element('xpath', "//div[contains(text(),'🥚')]" )
    cursor_style = egg_element.value_of_css_property('cursor')
    assert cursor_style == 'pointer'  # 계란 요소에 cursor: pointer 스타일이 적용되어야 함
