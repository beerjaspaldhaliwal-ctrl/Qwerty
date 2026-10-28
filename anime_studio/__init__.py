"""Anime Studio: turn a story script, character sheets and location art
into a fully staged, animated 2D anime episode in Blender."""

bl_info = {
    "name": "Anime Studio - Script to 2D Anime Episode",
    "author": "Anime Studio",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > Anime",
    "description": "Builds full 2D anime episodes (shots, acting, effects, edit) from a text script",
    "category": "Animation",
}

import os

if "bpy" in locals():
    import importlib
    for _m in (script_parser, planner, compat, materials, placeholder_art, builder, editor, voices, pipeline):
        importlib.reload(_m)
else:
    from . import script_parser, planner

try:
    import bpy
    from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
    from . import compat, materials, placeholder_art, builder, editor, voices, pipeline
except ImportError:          # imported outside Blender (unit tests)
    bpy = None

TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "example_script.txt")

if bpy is not None:

    class AnimeStudioSettings(bpy.types.PropertyGroup):
        source: EnumProperty(name="Script", items=[("FILE", "File", "Read the script from a text file"),
                                                   ("TEXT", "Text Editor", "Use a script open in Blender's Text Editor")],
                             default="FILE")
        script_path: StringProperty(name="Script File", subtype="FILE_PATH",
                                    description="Your story script (.txt)")
        script_text: StringProperty(name="Text", description="Text datablock holding the script")
        assets_dir: StringProperty(name="Assets Folder", subtype="DIR_PATH",
                                   description="Folder with characters/, locations/, voices/, sfx/, music/. "
                                               "Empty = the script's folder")
        output_path: StringProperty(name="Output", subtype="FILE_PATH", default="//anime_episode.mp4")
        engine: EnumProperty(name="Renderer", items=[
            ("EEVEE", "EEVEE (fast)", "Real-time renderer, recommended"),
            ("CYCLES", "Cycles", "Slower; works on machines without a capable GPU"),
            ("WORKBENCH", "Workbench (draft)", "Fastest draft preview")], default="EEVEE")
        quality: IntProperty(name="Resolution %", default=100, min=10, max=100, subtype="PERCENTAGE")
        drama: EnumProperty(name="Hype", items=[
            ("SCRIPT", "From Script", "Use STYLE: from the script"),
            ("0", "Calm", "Gentle camera, few effects"),
            ("1", "Normal", "Classic shonen pacing"),
            ("2", "Max Hype", "Dutch angles, reaction shots, extra impact")], default="SCRIPT")
        tts: BoolProperty(name="Placeholder Voices",
                          description="Generate voices with the computer's text-to-speech for lines "
                                      "that have no recording")

    def _settings(context):
        return context.scene.anime_studio

    def _script_args(st):
        if st.source == "TEXT":
            txt = bpy.data.texts.get(st.script_text)
            if not txt:
                raise ValueError("Pick a script in the Text Editor first")
            return dict(path=None, text=txt.as_string())
        path = bpy.path.abspath(st.script_path)
        if not path or not os.path.isfile(path):
            raise ValueError("Choose your script file first")
        return dict(path=path, text=None)

    def _assets(st):
        if st.assets_dir:
            return bpy.path.abspath(st.assets_dir)
        if st.source == "FILE" and st.script_path:
            return os.path.dirname(bpy.path.abspath(st.script_path))
        return bpy.path.abspath("//")

    def _write_report(lines):
        txt = bpy.data.texts.get("Anime Studio Report") or bpy.data.texts.new("Anime Studio Report")
        txt.clear()
        txt.write("\n".join(lines) + "\n")
        return txt

    class ANIMESTUDIO_OT_template(bpy.types.Operator):
        bl_idname = "anime_studio.write_template"
        bl_label = "Create Project Template"
        bl_description = "Create a project folder with an example script and the folder layout for your art"
        directory: StringProperty(subtype="DIR_PATH")

        def invoke(self, context, event):
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            root = bpy.path.abspath(self.directory)
            for sub in ("characters", "locations", "voices", "sfx", "music"):
                os.makedirs(os.path.join(root, sub), exist_ok=True)
            dst = os.path.join(root, "episode_01.txt")
            if not os.path.exists(dst):
                with open(TEMPLATE, encoding="utf-8") as src, open(dst, "w", encoding="utf-8") as out:
                    out.write(src.read())
            st = _settings(context)
            st.source = "FILE"
            st.script_path = dst
            st.assets_dir = root
            txt = bpy.data.texts.load(dst)
            self.report({"INFO"}, f"Template written to {root}. Script opened in the Text Editor as {txt.name}")
            return {"FINISHED"}

    class ANIMESTUDIO_OT_check(bpy.types.Operator):
        bl_idname = "anime_studio.check"
        bl_label = "Check Script"
        bl_description = "Read the script and report characters, scenes, length and any problems"

        def execute(self, context):
            st = _settings(context)
            try:
                script, plan, lines = pipeline.check(assets_dir=_assets(st), **_script_args(st))
            except Exception as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            _write_report(lines)
            level = {"ERROR"} if script.errors else ({"WARNING"} if script.warnings else {"INFO"})
            self.report(level, " | ".join(lines[:5]) + "  (full report: Text Editor > Anime Studio Report)")
            return {"FINISHED"}

    class ANIMESTUDIO_OT_build(bpy.types.Operator):
        bl_idname = "anime_studio.build"
        bl_label = "Build Episode"
        bl_description = "Create every scene, shot, character animation and effect, and edit them into the episode"

        def execute(self, context):
            st = _settings(context)
            saved = {k: getattr(st, k) for k in st.__annotations__}
            try:
                drama = None if st.drama == "SCRIPT" else int(st.drama)
                master, lines = pipeline.build(assets_dir=_assets(st), output_path=st.output_path,
                                               engine=st.engine, quality=st.quality, drama=drama, tts=st.tts,
                                               log=lambda m: print("Anime Studio:", m), **_script_args(st))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.report({"ERROR"}, f"Build failed: {e}")
                return {"CANCELLED"}
            for k, v in saved.items():
                setattr(master.anime_studio, k, v)
            context.window.scene = master
            _write_report(lines)
            self.report({"INFO"}, lines[4] if len(lines) > 4 else "Episode built")
            return {"FINISHED"}

    class ANIMESTUDIO_OT_render(bpy.types.Operator):
        bl_idname = "anime_studio.render"
        bl_label = "Render Episode"
        bl_description = "Render the finished episode to a video file"

        def execute(self, context):
            master = next((s for s in bpy.data.scenes if s.get(builder.TAG) == "episode"), None)
            if not master:
                self.report({"ERROR"}, "Build the episode first")
                return {"CANCELLED"}
            context.window.scene = master
            bpy.ops.render.render("INVOKE_DEFAULT", animation=True, scene=master.name)
            return {"FINISHED"}

    class ANIMESTUDIO_PT_main(bpy.types.Panel):
        bl_label = "Anime Studio"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Anime"

        def draw(self, context):
            st = _settings(context)
            col = self.layout.column(align=False)
            col.operator("anime_studio.write_template", icon="FILE_NEW")
            box = col.box()
            box.label(text="1. Story", icon="TEXT")
            box.prop(st, "source", expand=True)
            if st.source == "FILE":
                box.prop(st, "script_path", text="")
            else:
                box.prop_search(st, "script_text", bpy.data, "texts", text="")
            box.prop(st, "assets_dir")
            box.operator("anime_studio.check", icon="VIEWZOOM")
            box = col.box()
            box.label(text="2. Direction", icon="CAMERA_DATA")
            box.prop(st, "drama")
            box.prop(st, "tts")
            box.operator("anime_studio.build", icon="SEQUENCE")
            box = col.box()
            box.label(text="3. Render", icon="RENDER_ANIMATION")
            box.prop(st, "engine")
            box.prop(st, "quality")
            box.prop(st, "output_path", text="")
            box.operator("anime_studio.render", icon="RENDER_ANIMATION")

    CLASSES = (AnimeStudioSettings, ANIMESTUDIO_OT_template, ANIMESTUDIO_OT_check, ANIMESTUDIO_OT_build,
               ANIMESTUDIO_OT_render, ANIMESTUDIO_PT_main)

    def register():
        for c in CLASSES:
            bpy.utils.register_class(c)
        bpy.types.Scene.anime_studio = PointerProperty(type=AnimeStudioSettings)

    def unregister():
        del bpy.types.Scene.anime_studio
        for c in reversed(CLASSES):
            bpy.utils.unregister_class(c)
