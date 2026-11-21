// Foreground iPhone intercom for the independent Linux userspace.
package main

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"embed"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io/fs"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

//go:embed web/*
var assets embed.FS

const prefix = "/api/v1/intercom"
const cookieName = "__Host-echo2-room"

type config struct {
	backend, mic, speaker, privacy, dnd string
	enabled                             bool
	password                            [32]byte
}

type lease struct {
	id, ticket, owner string
	expiry            time.Time
	cancel            chan struct{}
	active, closing   bool
}

type stats struct {
	FromPhone    uint64 `json:"from_phone"`
	ToPhone      uint64 `json:"to_phone"`
	PhonePeak    int    `json:"phone_peak"`
	PhonePeakRMS int    `json:"phone_peak_rms"`
	Replies      uint64 `json:"probe_replies"`
}

type service struct {
	cfg      config
	mu       sync.Mutex
	sessions map[string]time.Time
	call     *lease
	last     stats
	failures int
	reset    time.Time
}

func randomID() string {
	var b [32]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}

func reply(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": status < 400, "data": data})
}

func (s *service) closeCallLocked() {
	if s.call == nil || s.call.closing {
		return
	}
	close(s.call.cancel)
	s.call.closing = true
	if !s.call.active {
		s.call = nil
	}
}

func (s *service) expireLocked(now time.Time) {
	for token, expiry := range s.sessions {
		if !now.Before(expiry) {
			delete(s.sessions, token)
		}
	}
	if s.call != nil {
		_, authenticated := s.sessions[s.call.owner]
		if !authenticated || !now.Before(s.call.expiry) {
			s.closeCallLocked()
		}
	}
}

func (s *service) cookie(r *http.Request) string {
	c, err := r.Cookie(cookieName)
	if err != nil {
		return ""
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expireLocked(time.Now())
	if _, ok := s.sessions[c.Value]; ok {
		return c.Value
	}
	return ""
}

func originOK(r *http.Request) bool {
	return r.TLS != nil && r.Header.Get("Origin") == "https://"+r.Host
}

func (s *service) permitted() bool {
	if !s.cfg.enabled {
		return false
	}
	if s.cfg.backend == "probe" {
		return true
	}
	for _, path := range []string{s.cfg.privacy, s.cfg.dnd} {
		b, err := os.ReadFile(path)
		if err != nil || strings.TrimSpace(string(b)) != "0" {
			return false
		}
	}
	return true
}

func (s *service) handler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	// A no-referrer document sends Origin: null for a native form navigation.
	// Keep the same-origin value for sign-in while suppressing cross-site referrers.
	w.Header().Set("Referrer-Policy", "same-origin")
	w.Header().Set("Permissions-Policy", "microphone=(self), camera=()")
	w.Header().Set("Content-Security-Policy", "default-src 'self'; connect-src 'self' wss:; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
	if r.TLS == nil {
		http.Error(w, "HTTPS required", http.StatusBadRequest)
		return
	}
	if r.URL.RawQuery != "" {
		http.NotFound(w, r)
		return
	}
	if r.Method == http.MethodPost || r.Method == http.MethodDelete {
		if !originOK(r) {
			http.Error(w, "Origin mismatch", http.StatusForbidden)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 2048)
	}
	if r.URL.Path == "/health" && r.Method == http.MethodGet {
		reply(w, 200, "ready")
		return
	}
	if r.URL.Path == "/login" && r.Method == http.MethodPost {
		s.mu.Lock()
		now := time.Now()
		if !now.Before(s.reset) {
			s.failures = 0
			s.reset = now.Add(time.Minute)
		}
		limited := s.failures >= 12
		if !limited {
			s.failures++
		}
		s.mu.Unlock()
		if limited {
			http.Error(w, "Try again in one minute", 429)
			return
		}
		if err := r.ParseForm(); err != nil {
			http.Error(w, "Invalid sign-in", 400)
			return
		}
		digest := sha256.Sum256([]byte(r.PostForm.Get("password")))
		if subtle.ConstantTimeCompare(digest[:], s.cfg.password[:]) != 1 {
			http.Error(w, "Passphrase did not match. Go back to try again.", 401)
			return
		}
		token := randomID()
		s.mu.Lock()
		s.expireLocked(now)
		if len(s.sessions) >= 16 {
			s.mu.Unlock()
			http.Error(w, "Too many sessions", 429)
			return
		}
		s.sessions[token] = now.Add(time.Hour)
		s.failures = 0
		s.mu.Unlock()
		http.SetCookie(w, &http.Cookie{Name: cookieName, Value: token, Path: "/", HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode, MaxAge: 3600})
		http.Redirect(w, r, "/intercom", http.StatusSeeOther)
		return
	}
	session := s.cookie(r)
	if r.URL.Path == "/" || r.URL.Path == "/intercom" {
		if r.Method != http.MethodGet {
			http.Error(w, "Use GET", 405)
			return
		}
		file := "web/index.html"
		if session == "" {
			file = "web/login.html"
		}
		page, _ := assets.ReadFile(file)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write(page)
		return
	}
	if r.URL.Path == "/style.css" && r.Method == http.MethodGet {
		serveAsset(w, "style.css", "text/css")
		return
	}
	if session == "" {
		reply(w, 401, nil)
		return
	}
	if r.URL.Path == "/logout" && r.Method == http.MethodPost {
		s.mu.Lock()
		delete(s.sessions, session)
		s.expireLocked(time.Now())
		s.mu.Unlock()
		http.SetCookie(w, &http.Cookie{Name: cookieName, Path: "/", MaxAge: -1, HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode})
		http.Redirect(w, r, "/", http.StatusSeeOther)
		return
	}
	if strings.HasPrefix(r.URL.Path, "/js/") && r.Method == http.MethodGet {
		n := strings.TrimPrefix(r.URL.Path, "/js/")
		switch n {
		case "intercom-ui.js", "intercom-worklet.js", "app.js":
			serveAsset(w, n, "text/javascript")
			return
		}
	}
	if r.URL.Path == prefix && r.Method == http.MethodGet {
		s.mu.Lock()
		defer s.mu.Unlock()
		reply(w, 200, map[string]any{"enabled": s.permitted(), "busy": s.call != nil, "backend": s.cfg.backend, "metrics": s.last})
		return
	}
	if r.URL.Path == prefix+"/call" && r.Method == http.MethodPost {
		s.mu.Lock()
		defer s.mu.Unlock()
		if !s.permitted() {
			reply(w, 403, nil)
			return
		}
		if s.call != nil {
			reply(w, 409, nil)
			return
		}
		c := &lease{id: randomID(), ticket: randomID(), owner: session, expiry: time.Now().Add(30 * time.Second), cancel: make(chan struct{})}
		s.call = c
		reply(w, 200, map[string]string{"id": c.id, "ticket": c.ticket, "path": prefix + "/stream"})
		return
	}
	if r.URL.Path == prefix+"/call" && r.Method == http.MethodDelete {
		var body struct {
			ID string `json:"id"`
		}
		if json.NewDecoder(r.Body).Decode(&body) != nil {
			reply(w, 400, nil)
			return
		}
		s.mu.Lock()
		defer s.mu.Unlock()
		if s.call != nil && s.call.owner == session && s.call.id == body.ID {
			s.closeCallLocked()
		}
		reply(w, 200, nil)
		return
	}
	if r.URL.Path == prefix+"/stream" && r.Method == http.MethodGet {
		s.stream(w, r, session)
		return
	}
	http.NotFound(w, r)
}

func serveAsset(w http.ResponseWriter, name, ct string) {
	b, err := fs.ReadFile(assets, "web/"+name)
	if err != nil {
		http.Error(w, "Asset unavailable", 500)
		return
	}
	w.Header().Set("Content-Type", ct)
	_, _ = w.Write(b)
}

func main() {
	var cfg config
	listen := flag.String("listen", "127.0.0.1:8443", "HTTPS address")
	cert := flag.String("cert", "", "TLS certificate chain in PEM format")
	key := flag.String("key", "", "TLS private key in PEM format")
	pass := flag.String("password-file", "", "single-account intercom passphrase file")
	flag.StringVar(&cfg.backend, "backend", "probe", "probe or fifo")
	flag.BoolVar(&cfg.enabled, "allow-calls", false, "allow foreground calls")
	flag.StringVar(&cfg.mic, "room-mic", "", "PCM capture FIFO, 16 kHz mono signed 16-bit LE")
	flag.StringVar(&cfg.speaker, "room-speaker", "", "PCM playback FIFO, same format")
	flag.StringVar(&cfg.privacy, "privacy-state", "", "FIFO mode: file containing 0 only while microphone use is allowed")
	flag.StringVar(&cfg.dnd, "dnd-state", "", "FIFO mode: file containing 0 only while calls are allowed")
	flag.Parse()
	if cfg.backend != "probe" && cfg.backend != "fifo" {
		log.Fatal("Choose probe or fifo backend")
	}
	if cfg.backend == "fifo" && (cfg.mic == "" || cfg.speaker == "" || cfg.mic == cfg.speaker || cfg.privacy == "" || cfg.dnd == "") {
		log.Fatal("FIFO backend needs distinct microphone and speaker FIFOs and both policy files")
	}
	b, err := os.ReadFile(*pass)
	if err != nil {
		log.Fatal("Passphrase file unavailable")
	}
	b = []byte(strings.TrimRight(string(b), "\r\n"))
	if len(b) < 12 || len(b) > 256 {
		log.Fatal("Use a 12 to 256 byte passphrase")
	}
	cfg.password = sha256.Sum256(b)
	s := &service{cfg: cfg, sessions: make(map[string]time.Time)}
	server := http.Server{Addr: *listen, Handler: http.HandlerFunc(s.handler), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 8192, TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
	fmt.Println("Echo 2 Linux intercom starting with", cfg.backend, "audio")
	log.Fatal(server.ListenAndServeTLS(*cert, *key))
}
