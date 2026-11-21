package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestDuplexFIFOAndPrivacy(t *testing.T) {
	dir := t.TempDir()
	cfg := config{backend: "fifo", enabled: true, password: sha256.Sum256([]byte("fixture-only-pass")),
		mic: filepath.Join(dir, "mic"), speaker: filepath.Join(dir, "speaker"),
		privacy: filepath.Join(dir, "privacy"), dnd: filepath.Join(dir, "dnd")}
	for _, name := range []string{cfg.privacy, cfg.dnd} {
		if err := os.WriteFile(name, []byte("0\n"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range []string{cfg.mic, cfg.speaker} {
		if err := syscall.Mkfifo(name, 0600); err != nil {
			t.Fatal(err)
		}
	}
	open := func(name string) *os.File {
		f, err := os.OpenFile(name, os.O_RDWR|syscall.O_NONBLOCK, 0)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { f.Close() })
		return f
	}
	source, sink := open(cfg.mic), open(cfg.speaker)
	s := &service{cfg: cfg, sessions: make(map[string]time.Time)}
	server := httptest.NewTLSServer(http.HandlerFunc(s.handler))
	defer server.Close()
	client := server.Client()
	client.Jar, _ = cookiejar.New(nil)
	send := func(method, path, body, contentType string) (int, []byte) {
		req, err := http.NewRequest(method, server.URL+path, strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Origin", server.URL)
		req.Header.Set("Content-Type", contentType)
		res, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		data, err := io.ReadAll(res.Body)
		if err != nil {
			t.Fatal(err)
		}
		return res.StatusCode, data
	}
	if status, _ := send("POST", "/login", "password=fixture-only-pass", "application/x-www-form-urlencoded"); status != 200 {
		t.Fatal("sign-in", status)
	}
	status, data := send("POST", prefix+"/call", "{}", "application/json")
	if status != 200 {
		t.Fatal("reserve", status)
	}
	var grant struct{ Data struct{ Path, Ticket string } }
	if err := json.Unmarshal(data, &grant); err != nil {
		t.Fatal(err)
	}
	status, _ = send("POST", prefix+"/call", "{}", "application/json")
	if status != 409 {
		t.Fatal("busy check", status)
	}
	dial := websocket.Dialer{TLSClientConfig: client.Transport.(*http.Transport).TLSClientConfig}
	headers := http.Header{"Origin": []string{server.URL}}
	u, _ := url.Parse(server.URL)
	for _, cookie := range client.Jar.Cookies(u) {
		headers.Add("Cookie", cookie.Name+"="+cookie.Value)
	}
	ws, _, err := dial.Dial("wss"+strings.TrimPrefix(server.URL, "https")+grant.Data.Path, headers)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	if err := ws.WriteJSON(map[string]string{"ticket": grant.Data.Ticket}); err != nil {
		t.Fatal(err)
	}
	ws.SetReadDeadline(time.Now().Add(2 * time.Second))
	if _, p, err := ws.ReadMessage(); err != nil || string(p) != "ready" {
		t.Fatal("room open", err)
	}
	capture, phone := make([]byte, 640), make([]byte, 644)
	for i := 0; i < 320; i++ {
		binary.LittleEndian.PutUint16(capture[2*i:], uint16(1000+i))
		binary.LittleEndian.PutUint16(phone[4+2*i:], uint16(5000+i))
	}
	if _, err = source.Write(capture[:173]); err != nil {
		t.Fatal(err)
	}
	time.Sleep(23 * time.Millisecond)
	if _, err = source.Write(capture[173:]); err != nil {
		t.Fatal(err)
	}
	if err = ws.WriteMessage(websocket.BinaryMessage, phone); err != nil {
		t.Fatal(err)
	}
	roomOut := make([]byte, 640)
	sink.SetReadDeadline(time.Now().Add(time.Second))
	if _, err = io.ReadFull(sink, roomOut); err != nil || !bytes.Equal(roomOut, phone[4:]) {
		t.Fatal("phone -> room speaker FIFO", err)
	}
	found := false
	for i := 0; i < 8; i++ {
		kind, p, err := ws.ReadMessage()
		if err != nil {
			t.Fatal(err)
		}
		if kind == websocket.BinaryMessage && len(p) == 644 && bytes.Equal(p[4:], capture) {
			found = true
			break
		}
	}
	if !found {
		t.Fatal("room microphone FIFO -> phone did not reproduce exact samples")
	}
	if err := os.WriteFile(cfg.privacy, []byte("1\n"), 0600); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 5; i++ {
		_, _, err = ws.ReadMessage()
		if err != nil {
			break
		}
	}
	if err == nil {
		t.Fatal("call continued with privacy latch closed")
	}
	status, _ = send("POST", prefix+"/call", "{}", "application/json")
	if status != 403 {
		t.Fatal("privacy failed to block new call", status)
	}
	if err := os.Remove(cfg.privacy); err != nil {
		t.Fatal(err)
	}
	if s.permitted() {
		t.Fatal("missing privacy state must block room access")
	}
	if err := os.WriteFile(cfg.privacy, []byte("0\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfg.dnd, []byte("1\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if s.permitted() {
		t.Fatal("DND must block room access")
	}
	t.Log("TLS/WSS phone PCM -> real named FIFO and split microphone FIFO PCM -> phone verified byte for byte; mute closed the call.")
}
