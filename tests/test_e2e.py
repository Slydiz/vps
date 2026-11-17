"""End-to-end: the real CLI (`python -m clipper demo`) as a separate process, driven over HTTP like a user's browser."""
import re, subprocess, sys, time, socket, pathlib
import pytest, requests

ROOT = pathlib.Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("e2e")
    port = free_port()
    p = subprocess.Popen([sys.executable, "-m", "clipper", "demo", "--data", str(data), "--port", str(port)],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(240):                                             # up to ~4 min: synth stream + 2 vertical clips
        try:
            if requests.get(base + "/api/state", timeout=1).ok:
                break
        except requests.RequestException:
            time.sleep(1)
        if p.poll() is not None:
            pytest.fail("server exited:\n" + p.stdout.read())
    else:
        p.kill(); pytest.fail("server did not start")
    yield base, data
    p.terminate(); p.wait(10)


def test_full_user_journey(server):
    base, data = server
    # 1. the demo stream produced two clips waiting in the review queue
    html = requests.get(base + "/review").text
    ids = [int(i) for i in re.findall(r'id="clip(\d+)"', html)]
    assert len(ids) == 2 and "score" in html
    # 2. the video file really plays (HTTP range request, as a phone browser does)
    src = re.search(r'<video[^>]+src="([^"]+)"', html).group(1)
    r = requests.get(base + src, headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206 and r.content[4:8] == b"ftyp"
    # 3. approve one with an edited caption, reject the other
    requests.post(f"{base}/clips/{ids[0]}/decide", data={"action": "approve", "caption": "e2e caption"}, allow_redirects=False)
    requests.post(f"{base}/clips/{ids[1]}/decide", data={"action": "reject"}, allow_redirects=False)
    for _ in range(30):                                              # publishing runs in a background thread
        st = requests.get(base + "/api/state").json()["counts"]
        if st["posted"] == 1:
            break
        time.sleep(0.5)
    assert st == {"pending": 0, "approved": 0, "rejected": 1, "posted": 1, "failed": 0, "manual": 0}
    # 4. the export folder holds the clip and its caption
    exported = list((data / "exports").glob("*.mp4"))
    assert len(exported) == 1 and exported[0].with_suffix(".txt").read_text() == "e2e caption"
    # 5. history page shows both outcomes
    hist = requests.get(base + "/history").text
    assert "posted" in hist and "rejected" in hist
