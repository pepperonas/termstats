//! A grid of cells and the cheapest way to get it onto a terminal.
//!
//! Every frame is drawn into `Screen`; `Painter` then writes ONLY the cells that differ
//! from what the terminal already shows, in one write, bracketed by synchronized-output
//! marks (DECSET 2026 - a terminal that does not know them ignores them). A dashboard at
//! rest changes a few dozen cells a frame; repainting all six thousand costs the terminal
//! emulator far more than it costs the program that sends them.

use crate::color::to_256;
use std::fmt::Write as _;

/// "No colour": the terminal's own foreground or background.
pub const NONE: u32 = 0xFF00_0000;

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Cell {
    pub ch: char,
    pub fg: u32,
    pub bg: u32,
    pub bold: bool,
}

pub const BLANK: Cell = Cell { ch: ' ', fg: NONE, bg: NONE, bold: false };

pub struct Screen {
    pub w: usize,
    pub h: usize,
    pub cells: Vec<Cell>,
}

impl Screen {
    pub fn new(w: usize, h: usize) -> Self {
        Screen { w, h, cells: vec![BLANK; w * h] }
    }

    pub fn reset(&mut self, w: usize, h: usize) {
        self.w = w;
        self.h = h;
        self.cells.clear();
        self.cells.resize(w * h, BLANK);
    }

    pub fn set(&mut self, x: usize, y: usize, ch: char, fg: u32, bold: bool) {
        if x < self.w && y < self.h {
            self.cells[y * self.w + x] = Cell { ch, fg, bg: NONE, bold };
        }
    }

    /// Write `text` from (x, y), never past column `limit` (exclusive). Returns the column
    /// after the last cell written.
    pub fn put(&mut self, x: usize, y: usize, limit: usize, text: &str, fg: u32, bold: bool) -> usize {
        let mut col = x;
        let limit = limit.min(self.w);
        if y >= self.h {
            return col;
        }
        for ch in text.chars() {
            if col >= limit {
                break;
            }
            self.cells[y * self.w + col] = Cell { ch, fg, bg: NONE, bold };
            col += 1;
        }
        col
    }

    /// The rows that differ from `prev`, a grid drawn earlier; every row when `prev` has
    /// another shape (120x36 and 108x40 hold the same number of cells, and must not be
    /// compared). Written into `out` (cleared first), ascending.
    pub fn changed_rows(&self, prev: &Screen, out: &mut Vec<u32>) {
        out.clear();
        let whole = prev.w != self.w || prev.h != self.h;
        for y in 0..self.h {
            let span = y * self.w..(y + 1) * self.w;
            if whole || self.cells[span.clone()] != prev.cells[span] {
                out.push(y as u32);
            }
        }
    }

    pub fn put_bg(&mut self, x: usize, y: usize, limit: usize, text: &str, fg: u32, bg: u32, bold: bool) -> usize {
        let end = self.put(x, y, limit, text, fg, bold);
        if y < self.h {
            for col in x..end {
                self.cells[y * self.w + col].bg = bg;
            }
        }
        end
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ColorLevel {
    True,
    Indexed,
    Mono,
}

pub struct Painter {
    shown: Vec<Cell>,
    w: usize,
    h: usize,
    pub out: String,
    pub level: ColorLevel,
}

/// Two changed runs closer than this are written as one: moving the cursor costs about as
/// many bytes as repeating a few cells.
const JOIN_GAP: usize = 4;

impl Painter {
    pub fn new(level: ColorLevel) -> Self {
        Painter { shown: Vec::new(), w: 0, h: 0, out: String::with_capacity(64 * 1024), level }
    }

    /// Forget what is on screen: the next frame is written whole (first frame, resize).
    pub fn invalidate(&mut self) {
        self.w = 0;
        self.h = 0;
    }

    fn sgr(&mut self, cell: &Cell) {
        self.out.push_str("\x1b[0");
        if cell.bold {
            self.out.push_str(";1");
        }
        for (colour, base) in [(cell.fg, 38), (cell.bg, 48)] {
            if colour == NONE {
                continue;
            }
            match self.level {
                ColorLevel::True => {
                    let _ = write!(self.out, ";{};2;{};{};{}", base, (colour >> 16) & 255, (colour >> 8) & 255, colour & 255);
                }
                ColorLevel::Indexed => {
                    let _ = write!(self.out, ";{};5;{}", base, to_256(colour));
                }
                ColorLevel::Mono => {}
            }
        }
        self.out.push('m');
    }

    /// The bytes that turn what the terminal shows into `screen`.
    pub fn diff(&mut self, screen: &Screen) -> &str {
        let full = self.w != screen.w || self.h != screen.h;
        if full {
            self.w = screen.w;
            self.h = screen.h;
            self.shown.clear();
            self.shown.resize(screen.w * screen.h, BLANK);
        }
        self.out.clear();
        self.out.push_str("\x1b[?2026h");
        if full {
            self.out.push_str("\x1b[0m\x1b[2J");
        }
        let w = screen.w;
        for y in 0..screen.h {
            let row = &screen.cells[y * w..(y + 1) * w];
            let mut x = 0;
            while x < w {
                if row[x] == self.shown[y * w + x] {
                    x += 1;
                    continue;
                }
                // The run ends where JOIN_GAP unchanged cells follow each other.
                let mut end = x + 1;
                let mut same = 0;
                let mut scan = x + 1;
                while scan < w && same < JOIN_GAP {
                    if row[scan] == self.shown[y * w + scan] {
                        same += 1;
                    } else {
                        same = 0;
                        end = scan + 1;
                    }
                    scan += 1;
                }
                let _ = write!(self.out, "\x1b[{};{}H", y + 1, x + 1);
                let mut style: Option<(u32, u32, bool)> = None;
                for cell in &row[x..end] {
                    let wanted = (cell.fg, cell.bg, cell.bold);
                    if style != Some(wanted) {
                        self.sgr(cell);
                        style = Some(wanted);
                    }
                    self.out.push(cell.ch);
                }
                self.out.push_str("\x1b[0m");
                self.shown[y * w + x..y * w + end].copy_from_slice(&row[x..end]);
                x = end;
            }
        }
        self.out.push_str("\x1b[?2026l");
        &self.out
    }

    /// The screen as plain lines - a snapshot for a pipe, a file or a scrollback.
    pub fn lines(&mut self, screen: &Screen) -> &str {
        self.out.clear();
        for y in 0..screen.h {
            let row = &screen.cells[y * screen.w..(y + 1) * screen.w];
            let used = row.iter().rposition(|c| *c != BLANK).map_or(0, |i| i + 1);
            let mut style: Option<(u32, u32, bool)> = None;
            for cell in &row[..used] {
                let wanted = (cell.fg, cell.bg, cell.bold);
                if self.level != ColorLevel::Mono && style != Some(wanted) {
                    self.sgr(cell);
                    style = Some(wanted);
                }
                self.out.push(cell.ch);
            }
            if style.is_some() {
                self.out.push_str("\x1b[0m");
            }
            self.out.push('\n');
        }
        &self.out
    }
}
