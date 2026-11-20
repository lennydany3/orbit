//! Draws the app's frame with Rune's own renderer and paints a sphere at every `effect` slot.
//!
//! A slot's mark says which sphere it is (so each keeps its own clock and blend) and its data
//! carries the [`SphereConfig`]: the dev stage and the small overlay orb are the same code with
//! different numbers.

use std::collections::HashMap;

use rune::compose::{Compositor, DeviceKey, Frame};

use super::controller::Orbit;
use super::gpu::{Gpu, Mesh, Uniforms};
use super::sim::{Sphere, SphereConfig};

/// App state that carries the orbit every sphere follows.
pub trait HasOrbit {
    fn orbit(&self) -> &Orbit;
}

struct Slot {
    sim: Sphere,
    mesh: Mesh,
    cfg: SphereConfig,
}

#[derive(Default)]
pub struct SphereCompositor {
    gpu: Option<(DeviceKey, Gpu)>,
    slots: HashMap<u32, Slot>,
    animating: bool,
}

impl<S: HasOrbit + 'static> Compositor<S> for SphereCompositor {
    fn animating(&self) -> bool {
        self.animating
    }

    fn render(&mut self, f: &mut Frame<'_, S>) {
        let key = f.device_key();
        if self.gpu.as_ref().is_none_or(|(k, _)| *k != key) {
            self.gpu = Some((key, Gpu::new(f.device)));
            self.slots.clear();
        }
        let (device, queue, clear) = (f.device, f.queue, f.rt.clear_color());
        f.ui.prepare(device, queue, &mut f.rt.text.atlas, &f.rt.instances, f.size);

        let orbit = f.rt.state.orbit().clone();
        let t = f.rt.t;
        let Some((_, gpu)) = self.gpu.as_mut() else { return };
        let slots = &mut self.slots;
        let mut seen: Vec<u32> = Vec::new();
        let mut busy = false;

        f.ui.draw_with(f.encoder, f.target, clear, &mut |pass, site| {
            let cfg = SphereConfig::from_data(site.data);
            seen.push(site.mark);
            gpu.ensure(site.format);
            let slot = slots.entry(site.mark).or_insert_with(|| Slot {
                sim: Sphere::new(&orbit),
                mesh: Mesh::new(gpu, cfg.count),
                cfg,
            });
            if slot.cfg.count != cfg.count {
                slot.mesh = Mesh::new(gpu, cfg.count);
            }
            slot.cfg = cfg;

            let rect = site.instance.rect;
            let local = [rect[0] - site.origin[0], rect[1] - site.origin[1], rect[2], rect[3]];
            let frame = slot.sim.step(&orbit, t, (rect[2], rect[3]), site.scale, &cfg);
            busy |= frame.busy;
            let uniforms = Uniforms::new(&frame, local, site.size, site.srgb_blend);
            if let Some(pipeline) = gpu.pipeline(site.format) {
                slot.mesh.draw(queue, pass, pipeline, &uniforms);
            }
        });

        slots.retain(|mark, _| seen.contains(mark));
        self.animating = busy;
    }
}
