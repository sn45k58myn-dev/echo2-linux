package main

import (
	"crypto/subtle"
	"encoding/binary"
	"encoding/json"
	"math"
	"net/http"
	"time"

	"github.com/gorilla/websocket"
)

// The speaker-facing goroutine is the only WSS writer; the phone reader uses
// a bounded channel so a stalled client cannot accumulate unbounded audio.
func (s *service) stream(w http.ResponseWriter, r *http.Request, session string) {
	if !originOK(r) {
		http.Error(w, "Origin mismatch", 403)
		return
	}
	up := websocket.Upgrader{CheckOrigin: originOK, HandshakeTimeout: 3 * time.Second}
	ws, err := up.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer ws.Close()
	ws.SetReadLimit(1024)
	_ = ws.SetReadDeadline(time.Now().Add(5 * time.Second))
	kind, initial, err := ws.ReadMessage()
	var auth struct {
		Ticket string `json:"ticket"`
	}
	if err != nil || kind != websocket.TextMessage || json.Unmarshal(initial, &auth) != nil || len(auth.Ticket) != 64 {
		return
	}
	s.mu.Lock()
	s.expireLocked(time.Now())
	c := s.call
	if c == nil || c.owner != session || subtle.ConstantTimeCompare([]byte(c.ticket), []byte(auth.Ticket)) != 1 || !s.permitted() {
		s.mu.Unlock()
		return
	}
	c.active = true
	c.ticket = ""
	c.expiry = time.Now().Add(10 * time.Minute)
	s.last = stats{}
	s.mu.Unlock()
	var metrics stats
	defer func() {
		s.mu.Lock()
		if s.call == c {
			s.last = metrics
			s.closeCallLocked()
			s.call = nil
		}
		s.mu.Unlock()
	}()
	device, e := openEndpoint(s.cfg)
	if e != nil {
		return
	}
	defer device.close()
	write := func(kind int, p []byte) error {
		_ = ws.SetWriteDeadline(time.Now().Add(400 * time.Millisecond))
		return ws.WriteMessage(kind, p)
	}
	if write(websocket.TextMessage, []byte("ready")) != nil {
		return
	}
	type packet struct {
		data []byte
		ping bool
	}
	input := make(chan packet, 8)
	done := make(chan struct{})
	defer close(done)
	go func() {
		defer close(input)
		var expected uint32
		for {
			_ = ws.SetReadDeadline(time.Now().Add(6 * time.Second))
			k, p, err := ws.ReadMessage()
			if err != nil {
				return
			}
			var event packet
			if k == websocket.TextMessage && string(p) == "ping" {
				event.ping = true
			} else if k == websocket.BinaryMessage && len(p) == 4+packetBytes && binary.BigEndian.Uint32(p) == expected {
				expected++
				event.data = p[4:]
			} else {
				return
			}
			select {
			case <-done:
				return
			case input <- event:
			default:
				return
			}
		}
	}()
	var seq uint32
	out := make([]byte, 4+packetBytes)
	ticks := time.NewTicker(20 * time.Millisecond)
	defer ticks.Stop()
	for {
		select {
		case <-c.cancel:
			return
		case p, ok := <-input:
			if !ok {
				return
			}
			if p.ping {
				if write(websocket.TextMessage, []byte("pong")) != nil {
					return
				}
				continue
			}
			if !s.permitted() || device.write(p.data) != nil {
				return
			}
			metrics.FromPhone++
			if v := peak(p.data); v > metrics.PhonePeak {
				metrics.PhonePeak = v
			}
			var squares float64
			for i := 0; i < len(p.data); i += 2 {
				v := float64(int16(binary.LittleEndian.Uint16(p.data[i:])))
				squares += v * v
			}
			if rms := int(math.Sqrt(squares / (packetBytes / 2))); rms > metrics.PhonePeakRMS {
				metrics.PhonePeakRMS = rms
			}
		case <-ticks.C:
			s.mu.Lock()
			s.expireLocked(time.Now())
			valid := s.call == c && !c.closing
			s.mu.Unlock()
			if !valid || !s.permitted() {
				return
			}
			replied, err := device.read(out[4:])
			if err != nil {
				return
			}
			binary.BigEndian.PutUint32(out, seq)
			seq++
			if write(websocket.BinaryMessage, out) != nil {
				return
			}
			metrics.ToPhone++
			if replied {
				metrics.Replies++
			}
			if seq%10 == 0 {
				s.mu.Lock()
				if s.call == c {
					s.last = metrics
				}
				s.mu.Unlock()
			}
		}
	}
}
