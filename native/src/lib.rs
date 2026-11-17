//! termstats-native as a library: the collectors, the dashboard and the cell grid it is
//! drawn into. The terminal program (`main.rs`) is one user; anything else that wants the
//! dashboard - a picture on the desktop, say - links the same code instead of a copy.

pub mod catalog;
pub mod color;
pub mod recommend;
pub mod record;
pub mod screen;
pub mod sys;
pub mod themes;
pub mod ui;
