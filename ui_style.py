"""Shared presentation tokens; no document or model logic."""
import gradio as gr

THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.teal,
    secondary_hue=gr.themes.colors.slate,
    neutral_hue=gr.themes.colors.slate,
    font=['Inter', 'Segoe UI', 'Arial', 'sans-serif'],
    font_mono=['Consolas', 'monospace'],
).set(
    body_background_fill='#f4f7f8', body_background_fill_dark='#101c26',
    block_background_fill='#ffffff', block_background_fill_dark='#172833',
    block_border_width='1px', block_radius='14px',
    button_primary_background_fill='#087e78', button_primary_background_fill_hover='#06665f',
    button_primary_text_color='#ffffff',
    button_primary_background_fill_dark='#087e78', button_primary_background_fill_hover_dark='#06665f',
    button_primary_text_color_dark='#ffffff',
)

CSS = '''
.gradio-container {max-width: 1180px !important; margin: auto; padding: 28px 24px !important;}
#brand {display:flex; align-items:center; gap:12px; padding: 4px 0 22px;}
#brand .mark {display:grid; place-items:center; width:40px; height:40px; border-radius:12px; background:#087e78; color:white; font-size:20px; font-weight:750;}
#brand .brand-name {font-size:21px; font-weight:750; letter-spacing:-.5px;}
#brand .brand-tag {font-size:13px; color:var(--body-text-color-subdued); margin-top:2px;}
#hero {padding:28px 32px; border-radius:20px; background:#132e3a; margin-bottom:12px;}
#hero .eyebrow {color:#86dcd0; font-size:11px; font-weight:700; letter-spacing:2px; margin:0 0 10px;}
#hero h1 {color:#ffffff !important; font-size:32px; line-height:1.25; letter-spacing:-.7px; margin:0 0 10px;}
#hero .subtitle {color:#c4d6dc; font-size:15px; max-width:700px; margin:0; line-height:1.6;}
#source-switch {margin:10px 0 0;}
#source-note {font-size:14px; padding:10px 14px; border-left:3px solid #087e78; background:var(--block-background-fill); border-radius:6px; line-height:1.6;}
#workflow > .tab-nav {gap:8px; border-bottom:1px solid var(--border-color-primary); padding-bottom:12px; margin-bottom:22px;}
#workflow > .tab-nav button {flex:1; padding:12px 18px; border-radius:10px; font-size:14px; font-weight:650;}
#workflow > .tab-nav button.selected {background:var(--button-primary-background-fill); color:white; border-color:transparent;}
.stage-intro {padding:4px 0 14px;}
.stage-intro h2 {font-size:23px !important; letter-spacing:-.5px; margin:0 0 5px !important;}
.stage-intro p {color:var(--body-text-color-subdued); font-size:15px; max-width:780px; margin:0; line-height:1.65;}
.surface {padding:22px !important; gap:16px !important; border:1px solid var(--border-color-primary) !important; border-radius:16px !important; background:var(--block-background-fill);}
.quiet-note {font-size:14px; color:var(--body-text-color-subdued); line-height:1.65;}
.empty-state {padding:16px 18px; border:1px dashed var(--border-color-primary); border-radius:12px; background:var(--block-background-fill); line-height:1.65;}
.document-text textarea {font-family:Inter, 'Segoe UI', Arial, sans-serif !important; font-size:15px !important; line-height:1.75 !important;}
.workflow-status {margin-top:18px; border-left:3px solid #087e78 !important;}
.gradio-container textarea {overflow-wrap:anywhere;}
.gradio-container button {white-space:normal;}
.gradio-container button:disabled {cursor:not-allowed;}
.gradio-container button {min-height:42px;}
.gradio-container button:focus-visible {outline:3px solid #20b7a7; outline-offset:3px;}
#footer-note {margin-top:22px; padding-top:14px; border-top:1px solid var(--border-color-primary); font-size:13px; color:var(--body-text-color-subdued); line-height:1.6;}
@media(max-width:640px) {
 .gradio-container {padding:16px 12px !important;}
 #hero {padding:23px 20px;}
 #hero h1 {font-size:28px;}
 #workflow > .tab-nav {gap:4px;}
 #workflow > .tab-nav button {padding:10px 6px; font-size:12px;}
 .surface {padding:14px !important;}
 .gradio-container .row {gap:14px;}
 .gradio-container .row > .surface {min-width:0 !important; flex-basis:100% !important;}
 #brand .brand-tag {font-size:12px;}
 #source-note {padding:10px 12px;}
}
'''


def stage_intro(title, description):
    gr.HTML(f'<div class="stage-intro"><h2>{title}</h2><p>{description}</p></div>')
