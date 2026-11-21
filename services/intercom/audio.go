package main

import (
	"encoding/binary"
	"errors"
	"math"
	"syscall"
	"time"
)

const packetBytes = 640

type endpoint interface {
	read([]byte) (bool, error)
	write([]byte) error
	close()
}

type pilot struct {
	tick                 uint64
	speaking             bool
	lastVoice, lastReply time.Time
	response             int
}

func peak(p []byte) int {
	high := 0
	for i := 0; i < len(p); i += 2 {
		v := int(int16(binary.LittleEndian.Uint16(p[i:])))
		if v < 0 {
			v = -v
		}
		if v > high {
			high = v
		}
	}
	return high
}
func (a *pilot) write(p []byte) error {
	if peak(p) > 1400 {
		a.speaking = true
		a.lastVoice = time.Now()
	}
	return nil
}
func (a *pilot) read(p []byte) (bool, error) {
	clear(p)
	frequency := 0
	replied := false
	if a.tick >= 2 && a.tick < 12 {
		frequency = 440
	}
	if a.speaking && time.Since(a.lastVoice) > 320*time.Millisecond && time.Since(a.lastReply) > 2*time.Second {
		a.response = 16
		a.speaking = false
		a.lastReply = time.Now()
		replied = true
	}
	if a.response > 0 {
		if a.response > 10 {
			frequency = 660
		} else if a.response <= 6 {
			frequency = 880
		}
		a.response--
	}
	if frequency != 0 {
		for i := 0; i < 320; i++ {
			v := int16(2400 * math.Sin(2*math.Pi*float64(frequency)*float64(a.tick*320+uint64(i))/16000))
			binary.LittleEndian.PutUint16(p[2*i:], uint16(v))
		}
	}
	a.tick++
	return replied, nil
}
func (*pilot) close() {}

type pipes struct {
	mic, speaker int
	pending      []byte
	lastMic      time.Time
}

func openFIFO(name string, flag int) (int, error) {
	fd, e := syscall.Open(name, flag|syscall.O_NONBLOCK|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0)
	if e != nil {
		return -1, e
	}
	var info syscall.Stat_t
	if e = syscall.Fstat(fd, &info); e != nil || info.Mode&syscall.S_IFMT != syscall.S_IFIFO {
		syscall.Close(fd)
		return -1, errors.New("audio path must be a named pipe")
	}
	return fd, nil
}
func openEndpoint(c config) (endpoint, error) {
	if c.backend == "probe" {
		return &pilot{}, nil
	}
	m, e := openFIFO(c.mic, syscall.O_RDONLY)
	if e != nil {
		return nil, e
	}
	s, e := openFIFO(c.speaker, syscall.O_WRONLY)
	if e != nil {
		syscall.Close(m)
		return nil, e
	}
	return &pipes{mic: m, speaker: s, lastMic: time.Now()}, nil
}
func (a *pipes) write(p []byte) error {
	n, e := syscall.Write(a.speaker, p)
	if e != nil {
		return e
	}
	if n != len(p) {
		return errors.New("incomplete playback packet")
	}
	return nil
}
func (a *pipes) read(p []byte) (bool, error) {
	clear(p)
	// A FIFO is a byte stream. Preserve a partial frame until more PCM arrives.
	buf := make([]byte, len(p)-len(a.pending))
	n, e := syscall.Read(a.mic, buf)
	if e != nil && e != syscall.EAGAIN && e != syscall.EINTR {
		return false, e
	}
	if n > 0 {
		a.pending = append(a.pending, buf[:n]...)
		a.lastMic = time.Now()
	}
	if len(a.pending) == len(p) {
		copy(p, a.pending)
		a.pending = a.pending[:0]
		return false, nil
	}
	if time.Since(a.lastMic) > 600*time.Millisecond {
		return false, errors.New("no room microphone frames")
	}
	return false, nil
}
func (a *pipes) close() { syscall.Close(a.mic); syscall.Close(a.speaker) }
