from html.parser import HTMLParser
import io

import pytest

from test_app import fields, post_csv
from test_json_input import encode, record
from test_telemetry import page_data


class Modules(HTMLParser):
    def __init__(self):
        super().__init__()
        self.panels={}
        self.tabs=[]

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'module-panel' in attrs.get('class','').split():
            self.panels[attrs['data-module']]=attrs
        if attrs.get('role')=='tab' and 'data-module-target' in attrs:
            self.tabs.append(attrs['data-module-target'])


def assert_active(response, active):
    modules=Modules()
    modules.feed(response.get_data(as_text=True))
    expected=['protocol','submit','validation','results','curves','leaderboard']
    assert modules.tabs==expected
    assert set(modules.panels)==set(expected)
    assert [name for name,attrs in modules.panels.items() if 'hidden' not in attrs]==[active]
    assert page_data(response)['meta']['active_module']==active


def test_initial_workspace_has_six_modules_and_one_visible_panel(client):
    response=client.get('/?mission=Mission1&source=demo')
    assert_active(response,'protocol')
    assert 'workspace.js' in response.get_data(as_text=True)
    assert 'workspace.css' in response.get_data(as_text=True)


@pytest.mark.parametrize('module',['submit','validation','results','leaderboard','curves'])
def test_direct_module_entry(client,module):
    response=client.get('/',query_string=dict(mission='Mission1',source='demo',module=module))
    assert_active(response,module)


def test_async_preview_returns_complete_patchable_panels_without_ranking(client):
    content=b'channel,starttime,endtime\nchannel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z\n'
    response=client.post('/',data=dict(fields(),action='preview',prediction_csv=(io.BytesIO(content),'a.csv')),
                         headers={'X-Requested-With':'fetch'})
    assert_active(response,'validation')
    meta=page_data(response)['meta']
    assert meta['has_report'] and not meta['report_has_errors'] and not meta['has_result']
    assert not (client.application.config['LEADERBOARD_DIR']/'leaderboard.json').exists()


def test_successful_evaluation_prefers_results_module(client):
    response=post_csv(client)
    assert_active(response,'results')
    assert page_data(response)['meta']['has_result']
    assert 'id="channel-scores"' in response.get_data(as_text=True)


def test_invalid_json_prefers_validation_and_keeps_error_positions(client):
    response=client.post('/',data=dict(fields(mode='json'),prediction_json=(io.BytesIO(encode([record(channel='bad')])),'bad.json')))
    assert_active(response,'validation')
    assert page_data(response)['meta']['report_has_errors']
    assert 'JSON 记录 1' in response.get_data(as_text=True)


def test_identity_error_returns_to_submit_not_empty_results(client):
    response=client.post('/',data=dict(fields(mode='manual'),user_id=''))
    assert_active(response,'submit')
    assert page_data(response)['meta']['identity_error']


def test_missing_upload_returns_to_submit_and_configuration_errors_to_protocol(client):
    response=client.post('/',data=fields())
    assert_active(response,'submit')
    assert page_data(response)['meta']['error']
    invalid=client.post('/',data=dict(fields(),version='old-version'))
    assert_active(invalid,'protocol')


def test_readonly_ranking_opens_its_module_without_evaluating(client):
    response=client.post('/',data=dict(fields(),action='leaderboard'))
    assert_active(response,'leaderboard')
    assert not page_data(response)['meta']['has_result']


def test_unified_leaderboard_is_independent_of_active_configuration(client):
    post_csv(client, 'Mission1', user_id='mission-one')
    post_csv(client, 'Mission2', user_id='mission-two')
    for response in (client.get('/?mission=Mission3&source=demo&module=leaderboard'),
                     client.get('/leaderboard?scope=legacy-key')):
        text=response.get_data(as_text=True)
        assert response.status_code==200
        assert 'data-user-id="mission-one"' in text and 'data-user-id="mission-two"' in text
        assert 'id="board-scope"' not in text
        assert '查看全部' not in text and '查看当前配置排行榜' not in text
