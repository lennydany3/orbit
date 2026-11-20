//! The Orbit sphere: states and their parameters, the controller every sphere follows, the
//! per-sphere simulation, and the GPU drawing.

pub mod compositor;
pub mod controller;
pub mod ease;
pub mod geometry;
pub mod gpu;
pub mod params;
pub mod sim;
pub mod state;

pub use compositor::{HasOrbit, SphereCompositor};
pub use controller::Orbit;
pub use sim::SphereConfig;
pub use state::{ALL as STATES, OrbState};
