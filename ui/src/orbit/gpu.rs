//! The sphere's GPU side: one pipeline per target format, and per sphere its point buffers and a
//! uniform buffer. Points are instanced quads (six vertices each) because wgpu has no point sprites.

use std::collections::HashMap;

use bytemuck::{Pod, Zeroable, bytes_of, cast_slice};
use wgpu::util::DeviceExt;

use super::geometry::sphere_points;
use super::sim::Frame;

/// Matches `struct U` in `sphere.wgsl`: every member a vec4, so there is no padding to get wrong.
#[repr(C)]
#[derive(Clone, Copy, Debug, Pod, Zeroable)]
pub struct Uniforms {
    rot: [[f32; 4]; 3],
    a: [[f32; 4]; 8],
    slot: [f32; 4],
    target_px: [f32; 4],
}

impl Uniforms {
    /// `slot` is the sphere's rectangle in the pass's pixels (x, y, w, h), `target` the pass's size.
    pub fn new(f: &Frame, slot: [f32; 4], target: [u32; 2], srgb_blend: bool) -> Uniforms {
        let col = |c: [f32; 3]| [c[0], c[1], c[2], 0.0];
        Uniforms {
            rot: [col(f.rot[0]), col(f.rot[1]), col(f.rot[2])],
            a: [
                [f.view[0], f.view[1], f.radius, f.px],
                [f.t, f.tw, f.flow_t, f.clock],
                [f.noise, f.freq, f.ripple, f.wave],
                [f.scan, f.scan_pos, f.swirl, f.flow],
                [f.loose, f.thick, f.jitter, f.burst],
                [f.flatten, f.sweep, f.sweep_pos, f.arc],
                [f.arc_prog, f.amber, f.pulse, f.red],
                [f.bright, f.rim, f.interior, if srgb_blend { 1.0 } else { 0.0 }],
            ],
            slot,
            target_px: [target[0] as f32, target[1] as f32, 0.0, 0.0],
        }
    }
}

/// The shader, the bind group layout, and a pipeline for each format a pass has asked for.
pub struct Gpu {
    device: wgpu::Device,
    module: wgpu::ShaderModule,
    bgl: wgpu::BindGroupLayout,
    layout: wgpu::PipelineLayout,
    pipelines: HashMap<wgpu::TextureFormat, wgpu::RenderPipeline>,
}

impl Gpu {
    pub fn new(device: &wgpu::Device) -> Gpu {
        let module = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("orbit sphere"),
            source: wgpu::ShaderSource::Wgsl(include_str!("sphere.wgsl").into()),
        });
        let bgl = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("orbit sphere"),
            entries: &[wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::VERTEX,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Uniform,
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            }],
        });
        let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("orbit sphere"),
            bind_group_layouts: &[Some(&bgl)],
            immediate_size: 0,
        });
        Gpu { device: device.clone(), module, bgl, layout, pipelines: HashMap::new() }
    }

    /// Builds the pipeline for `format` if no pass has needed it yet.
    pub fn ensure(&mut self, format: wgpu::TextureFormat) {
        if self.pipelines.contains_key(&format) {
            return;
        }
        let add = wgpu::BlendComponent {
            src_factor: wgpu::BlendFactor::One,
            dst_factor: wgpu::BlendFactor::One,
            operation: wgpu::BlendOperation::Add,
        };
        let pipeline = self.device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("orbit sphere"),
            layout: Some(&self.layout),
            vertex: wgpu::VertexState {
                module: &self.module,
                entry_point: Some("vs"),
                compilation_options: Default::default(),
                buffers: &[
                    Some(wgpu::VertexBufferLayout {
                        array_stride: 12,
                        step_mode: wgpu::VertexStepMode::Instance,
                        attributes: &wgpu::vertex_attr_array![0 => Float32x3],
                    }),
                    Some(wgpu::VertexBufferLayout {
                        array_stride: 16,
                        step_mode: wgpu::VertexStepMode::Instance,
                        attributes: &wgpu::vertex_attr_array![1 => Float32x4],
                    }),
                ],
            },
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: Default::default(),
            fragment: Some(wgpu::FragmentState {
                module: &self.module,
                entry_point: Some("fs"),
                compilation_options: Default::default(),
                targets: &[Some(wgpu::ColorTargetState {
                    format,
                    blend: Some(wgpu::BlendState { color: add, alpha: add }),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
            }),
            multiview_mask: None,
            cache: None,
        });
        self.pipelines.insert(format, pipeline);
    }

    pub fn pipeline(&self, format: wgpu::TextureFormat) -> Option<&wgpu::RenderPipeline> {
        self.pipelines.get(&format)
    }
}

/// One sphere's buffers: its points, and the uniforms it is drawn with.
pub struct Mesh {
    pub points: usize,
    dir: wgpu::Buffer,
    rnd: wgpu::Buffer,
    uniform: wgpu::Buffer,
    bind: wgpu::BindGroup,
}

impl Mesh {
    pub fn new(gpu: &Gpu, count: usize) -> Mesh {
        let device = &gpu.device;
        let pts = sphere_points(count);
        let dir: Vec<[f32; 3]> = pts.iter().map(|p| p.dir).collect();
        let rnd: Vec<[f32; 4]> = pts.iter().map(|p| p.rnd).collect();
        let dir_buf = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("orbit dir"),
            contents: cast_slice(&dir),
            usage: wgpu::BufferUsages::VERTEX,
        });
        let rnd_buf = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("orbit rnd"),
            contents: cast_slice(&rnd),
            usage: wgpu::BufferUsages::VERTEX,
        });
        let uniform = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("orbit uniforms"),
            size: size_of::<Uniforms>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        let bind = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("orbit sphere"),
            layout: &gpu.bgl,
            entries: &[wgpu::BindGroupEntry { binding: 0, resource: uniform.as_entire_binding() }],
        });
        Mesh { points: pts.len(), dir: dir_buf, rnd: rnd_buf, uniform, bind }
    }

    /// Records the draw. The uniforms go through the queue, so they land before the frame's commands run.
    pub fn draw(&self, queue: &wgpu::Queue, pass: &mut wgpu::RenderPass<'_>, pipeline: &wgpu::RenderPipeline, u: &Uniforms) {
        queue.write_buffer(&self.uniform, 0, bytes_of(u));
        pass.set_pipeline(pipeline);
        pass.set_bind_group(0, &self.bind, &[]);
        pass.set_vertex_buffer(0, self.dir.slice(..));
        pass.set_vertex_buffer(1, self.rnd.slice(..));
        pass.draw(0..6, 0..self.points as u32);
    }
}
