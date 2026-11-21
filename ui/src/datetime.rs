//! Wall-clock dates and times, the way reminders write them: local time on the clock on your wall, `2026-11-23T17:00`, with no time
//! zone. (A reminder for 9 in the morning is for 9 in the morning wherever the PC is.) Python reads and writes the same text
//! (`orbit/userdata.py`), and the rules for the next time of a repeating reminder are the same on both sides.
//!
//! No crate: the calendar arithmetic is the standard days-from-civil algorithm, and the only call to the system is `GetLocalTime`.

use std::fmt;

pub const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
pub const WEEKDAYS: [&str; 7] = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/// A date and a time of day, as written on a wall. Orders by time (year first, then month, and so on).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Stamp {
    pub year: i32,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
}

pub fn days_in_month(year: i32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) => 29,
        _ => 28,
    }
}

/// Days since 1970-01-01 (negative before it) for a date.
fn days_from_civil(year: i32, month: u32, day: u32) -> i64 {
    let y = i64::from(year) - i64::from(month <= 2);
    let era = y.div_euclid(400);
    let yoe = y.rem_euclid(400);
    let mp = (i64::from(month) + 9) % 12;
    let doy = (153 * mp + 2) / 5 + i64::from(day) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn civil_from_days(days: i64) -> (i32, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = (yoe + era * 400 + i64::from(month <= 2)) as i32;
    (year, month, day)
}

impl Stamp {
    pub fn new(year: i32, month: u32, day: u32, hour: u32, minute: u32, second: u32) -> Option<Stamp> {
        let ok = (1970..=9999).contains(&year) && (1..=12).contains(&month) && day >= 1 && day <= days_in_month(year, month) && hour < 24 && minute < 60 && second < 60;
        ok.then_some(Stamp { year, month, day, hour, minute, second })
    }

    /// `2026-11-23T17:00` or `2026-11-23T17:00:30`. Anything else is not a time.
    pub fn parse(text: &str) -> Option<Stamp> {
        let (date, time) = text.split_once('T')?;
        let mut d = date.split('-');
        let (year, month, day) = (d.next()?, d.next()?, d.next()?);
        if d.next().is_some() || year.len() != 4 || month.len() > 2 || day.len() > 2 {
            return None;
        }
        let mut t = time.split(':');
        let (hour, minute) = (t.next()?, t.next()?);
        let second = t.next();
        if t.next().is_some() || hour.len() != 2 || minute.len() != 2 || second.is_some_and(|s| s.len() != 2) {
            return None;
        }
        let number = |s: &str| s.parse::<u32>().ok().filter(|_| s.bytes().all(|b| b.is_ascii_digit()));
        Stamp::new(year.parse().ok()?, number(month)?, number(day)?, number(hour)?, number(minute)?, second.map_or(Some(0), number)?)
    }

    /// The one way a time is written in the files: seconds only if there are some.
    pub fn to_text(&self) -> String {
        let base = format!("{:04}-{:02}-{:02}T{:02}:{:02}", self.year, self.month, self.day, self.hour, self.minute);
        if self.second == 0 { base } else { format!("{base}:{:02}", self.second) }
    }

    /// Days since 1970-01-01.
    pub fn day_number(&self) -> i64 {
        days_from_civil(self.year, self.month, self.day)
    }

    pub fn from_day_number(days: i64, hour: u32, minute: u32, second: u32) -> Stamp {
        let (year, month, day) = civil_from_days(days);
        Stamp { year, month, day, hour, minute, second }
    }

    /// 0 for Monday ... 6 for Sunday.
    pub fn weekday(&self) -> u32 {
        (self.day_number() + 3).rem_euclid(7) as u32
    }

    pub fn add_days(&self, days: i64) -> Stamp {
        Stamp::from_day_number(self.day_number() + days, self.hour, self.minute, self.second)
    }

    pub fn add_minutes(&self, minutes: i64) -> Stamp {
        let total = self.day_number() * 1440 + i64::from(self.hour) * 60 + i64::from(self.minute) + minutes;
        Stamp::from_day_number(total.div_euclid(1440), (total.rem_euclid(1440) / 60) as u32, (total.rem_euclid(60)) as u32, self.second)
    }

    /// `self` is this many seconds after `other` (negative if before).
    pub fn seconds_since(&self, other: &Stamp) -> i64 {
        let secs = |s: &Stamp| s.day_number() * 86_400 + i64::from(s.hour) * 3600 + i64::from(s.minute) * 60 + i64::from(s.second);
        secs(self) - secs(other)
    }

    /// The same day at another time.
    pub fn at(&self, hour: u32, minute: u32) -> Stamp {
        Stamp { hour, minute, second: 0, ..*self }
    }

    /// The same time of day, `months` later; a day that month does not have ("31st") becomes its last.
    pub fn add_months(&self, months: i64) -> Stamp {
        let index = i64::from(self.year) * 12 + i64::from(self.month) - 1 + months;
        let (year, month) = (index.div_euclid(12) as i32, (index.rem_euclid(12) + 1) as u32);
        Stamp { year, month, day: self.day.min(days_in_month(year, month)), ..*self }
    }

    pub fn same_day(&self, other: &Stamp) -> bool {
        (self.year, self.month, self.day) == (other.year, other.month, other.day)
    }

    /// Now, on the clock on the wall.
    pub fn now() -> Stamp {
        os::local_now().and_then(|(y, mo, d, h, mi, s)| Stamp::new(y, mo, d, h, mi, s)).unwrap_or_else(|| {
            // No local clock to ask (not Windows): UTC, which is right for the tests that are all this is used for there.
            let secs = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs() as i64);
            Stamp::from_day_number(secs.div_euclid(86_400), (secs.rem_euclid(86_400) / 3600) as u32, (secs.rem_euclid(3600) / 60) as u32, (secs.rem_euclid(60)) as u32)
        })
    }
}

impl fmt::Display for Stamp {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_text())
    }
}

#[cfg(windows)]
mod os {
    #[repr(C)]
    #[derive(Default)]
    struct SystemTime {
        year: u16,
        month: u16,
        day_of_week: u16,
        day: u16,
        hour: u16,
        minute: u16,
        second: u16,
        milliseconds: u16,
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetLocalTime(time: *mut SystemTime);
    }

    pub fn local_now() -> Option<(i32, u32, u32, u32, u32, u32)> {
        let mut t = SystemTime::default();
        // SAFETY: `t` is a SYSTEMTIME (eight 16-bit fields, as declared above) that lives for the whole call, which only fills it in.
        unsafe { GetLocalTime(&mut t) };
        Some((i32::from(t.year), u32::from(t.month), u32::from(t.day), u32::from(t.hour), u32::from(t.minute), u32::from(t.second)))
    }
}

#[cfg(not(windows))]
mod os {
    pub fn local_now() -> Option<(i32, u32, u32, u32, u32, u32)> {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(text: &str) -> Stamp {
        Stamp::parse(text).unwrap()
    }

    #[test]
    fn a_time_is_written_and_read_the_way_the_other_side_does() {
        assert_eq!(at("2026-11-22T17:00").to_text(), "2026-11-22T17:00");
        assert_eq!(at("2026-11-22T17:00:30").to_text(), "2026-11-22T17:00:30");
        assert_eq!(at("2026-11-22T17:00:00").to_text(), "2026-11-22T17:00", "seconds only if there are some");
        for bad in ["", "2026-11-22", "2026-11-22 17:00", "2026-13-01T10:00", "2026-02-30T10:00", "2026-11-22T24:00", "2026-11-22T10:60", "26-11-22T10:00",
                    "2026-11-22T1:00", "2026-11-22T10:00:5", "2026-11-22T10:00:00:00", "2026-1-1-1T10:00", "2026-11-22T10:00Z", "+026-11-22T10:00", "abcd-11-22T10:00"] {
            assert_eq!(Stamp::parse(bad), None, "{bad:?}");
        }
        assert_eq!(Stamp::parse("2026-1-5T09:05").map(|s| s.to_text()), Some("2026-01-05T09:05".into()), "one-digit months are read, and written with two");
    }

    #[test]
    fn the_calendar_is_right() {
        assert_eq!(at("1970-01-01T00:00").day_number(), 0);
        assert_eq!(at("2000-03-01T00:00").day_number(), 11_017);
        assert_eq!(Stamp::from_day_number(11_017, 0, 0, 0).to_text(), "2000-03-01T00:00");
        for days in [-1, 0, 1, 59, 60, 365, 366, 10_957, 19_000, 25_000, 2_932_896] {
            let s = Stamp::from_day_number(days, 0, 0, 0);
            assert_eq!(s.day_number(), days, "{s}");
        }
        assert_eq!((days_in_month(2024, 2), days_in_month(2026, 2), days_in_month(2100, 2), days_in_month(2000, 2)), (29, 28, 28, 29));
        assert_eq!(at("2026-11-22T00:00").weekday(), 6, "a Sunday");
        assert_eq!(at("2026-11-23T00:00").weekday(), 0);
        assert_eq!(at("1970-01-01T00:00").weekday(), 3, "a Thursday");
    }

    #[test]
    fn adding_days_minutes_and_months_carries_over() {
        assert_eq!(at("2026-12-31T23:30").add_minutes(45).to_text(), "2027-01-01T00:15");
        assert_eq!(at("2026-11-22T00:10").add_minutes(-20).to_text(), "2026-11-21T23:50");
        assert_eq!(at("2026-02-27T10:00").add_days(3).to_text(), "2026-03-02T10:00");
        assert_eq!(at("2026-01-31T09:00").add_months(1).to_text(), "2026-02-28T09:00");
        assert_eq!(at("2026-11-30T09:00").add_months(3).to_text(), "2027-02-28T09:00");
        assert_eq!(at("2026-01-15T09:00").add_months(-2).to_text(), "2025-11-15T09:00");
    }

    #[test]
    fn times_order_by_the_clock_and_differences_are_in_seconds() {
        assert!(at("2026-11-22T17:00") < at("2026-11-22T17:00:01") && at("2026-11-22T23:59") < at("2026-11-23T00:00") && at("2026-12-01T00:00") < at("2027-01-01T00:00"));
        assert_eq!(at("2026-11-22T17:01").seconds_since(&at("2026-11-22T17:00")), 60);
        assert_eq!(at("2026-11-23T00:00").seconds_since(&at("2026-11-22T23:59:30")), 30);
        assert!(at("2026-11-22T08:00").same_day(&at("2026-11-22T23:00")) && !at("2026-11-22T23:59").same_day(&at("2026-11-23T00:00")));
    }

    #[test]
    fn now_is_a_real_time() {
        let now = Stamp::now();
        assert!(now.year >= 2024 && Stamp::parse(&now.to_text()) == Some(now));
    }
}
