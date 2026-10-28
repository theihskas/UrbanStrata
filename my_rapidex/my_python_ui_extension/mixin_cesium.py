"""
mixin_cesium.py — Cesium setup and camera methods mixed into the extension class.
"""
import asyncio
import carb
import omni.usd
from pxr import UsdGeom, Gf

from my_rapidex.my_python_ui_extension.network_ops import (
    get_city_coordinates, get_city_elevation,
)


class CesiumMixin:

    async def _startup_cesium(self):
        import omni.kit.app
        import omni.kit.window.file
        app = omni.kit.app.get_app()
        for _ in range(10):
            await app.next_update_async()

        ext_manager = app.get_extension_manager()
        settings = carb.settings.get_settings()

        try:
            if not ext_manager.is_extension_enabled("cesium.omniverse"):
                ext_manager.set_extension_enabled_immediate("cesium.omniverse", True)
                for _ in range(5):
                    await app.next_update_async()
        except Exception as exc:
            carb.log_warn(f"[my_rapidex] Could not enable cesium.omniverse: {exc}")

        if not settings.get_as_bool("/app/useFabricSceneDelegate"):
            settings.set_bool("/app/useFabricSceneDelegate", True)
            settings.set_bool("/persistent/app/useFabricSceneDelegate", True)
            settings.set_bool("/app/usdrt/scene_delegate/enableProxyCubes", False)
            settings.set_bool("/app/usdrt/scene_delegate/geometryStreaming/enabled", False)
            settings.set_bool("/omnihydra/parallelHydraSprimSync", False)
            carb.log_info("Fabric Scene Delegation enabled")
            self._cesium_stage_sub = (
                omni.usd.get_context()
                .get_stage_event_stream()
                .create_subscription_to_pop(
                    self._on_stage_opened_for_cesium,
                    name="rapidex_cesium_stage_ready",
                )
            )
            await asyncio.sleep(1.0)
            await omni.usd.get_context().new_stage_async()
            return

        for _ in range(3):
            await app.next_update_async()
        await self._add_cesium_tileset()

    def _on_stage_opened_for_cesium(self, event):
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._cesium_stage_sub = None
            self._run_async(self._add_cesium_tileset())

    async def _add_cesium_tileset(self):
        import omni.kit.app
        app  = omni.kit.app.get_app()
        loop = asyncio.get_event_loop()
        for _ in range(5):
            await app.next_update_async()

        TOKEN = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
                 ".eyJqdGkiOiIzYWE3ZTJiOC0wNWQ5LTRkZjMtYTk0ZS01NDhmZmI4MjFiNWMiLCJpZCI6MzQ4MzQ5"
                 "LCJpYXQiOjE3NTk5MjYzNTZ9.jeJoumgmh5J2cfnj5q6ebCcrotbg7FUgbdS_pWkjiU0")
        try:
            from cesium.usd.plugins.CesiumUsdSchemas import (
                Data as CesiumData,
                IonServer as CesiumIonServer,
                Tileset as CesiumTileset,
            )
            from cesium.omniverse.usdUtils.usdUtils import (
                get_or_create_cesium_data,
                get_or_create_cesium_georeference,
                add_tileset_ion,
            )

            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return
            get_or_create_cesium_data()

            SERVER_PATH = "/CesiumServers/IonOfficial"
            servers = [x for x in stage.Traverse() if x.IsA(CesiumIonServer)]
            if not servers:
                sp = CesiumIonServer.Define(stage, SERVER_PATH)
                sp.GetDisplayNameAttr().Set("ion.cesium.com")
                sp.GetIonServerUrlAttr().Set("https://ion.cesium.com/")
                sp.GetIonServerApiUrlAttr().Set("https://api.cesium.com/")
                sp.GetIonServerApplicationIdAttr().Set(413)
            else:
                SERVER_PATH = servers[0].GetPath().pathString
                sp = CesiumIonServer.Get(stage, SERVER_PATH)
            sp.GetProjectDefaultIonAccessTokenAttr().Set(TOKEN)
            CesiumData.Get(stage, "/Cesium").GetSelectedIonServerRel().SetTargets([SERVER_PATH])

            city_name = self._city_input.model.get_value_as_string() or self._city_name
            lat, lon = await loop.run_in_executor(
                None, lambda: get_city_coordinates(city_name))
            elev = get_city_elevation(city_name)

            georef = get_or_create_cesium_georeference()
            georef.GetGeoreferenceOriginLatitudeAttr().Set(lat)
            georef.GetGeoreferenceOriginLongitudeAttr().Set(lon)
            georef.GetGeoreferenceOriginHeightAttr().Set(float(elev))
            carb.log_info(f"[my_rapidex] Cesium georeference: {city_name} lat={lat:.4f} lon={lon:.4f}")

            existing = [x for x in stage.Traverse() if x.IsA(CesiumTileset)]
            if not any(
                CesiumTileset.Get(stage, x.GetPath()).GetIonAssetIdAttr().Get() == 2275207
                for x in existing
            ):
                add_tileset_ion("Google Photorealistic 3D Tiles", 2275207, TOKEN)

            await self._fly_camera_to_georeference()
        except Exception as exc:
            import traceback
            carb.log_error(f"[my_rapidex] Cesium setup failed: {exc}\n{traceback.format_exc()}")

    async def _fly_camera_to_georeference(self):
        try:
            import omni.kit.app
            from omni.kit.viewport.utility import get_active_viewport
            await omni.kit.app.get_app().next_update_async()
            viewport = get_active_viewport()
            if viewport is None:
                return
            camera_path = viewport.camera_path
            if not camera_path or str(camera_path) in ("", "<invalid>"):
                return
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return
            cam = stage.GetPrimAtPath(str(camera_path))
            if not cam.IsValid():
                return
            xformable = UsdGeom.Xformable(cam)
            existing = {op.GetOpName(): op for op in xformable.GetOrderedXformOps()}
            t_op = existing.get("xformOp:translate") or xformable.AddTranslateOp()
            t_op.Set(Gf.Vec3d(0.0, -1000.0, 2000.0))
            r_op = existing.get("xformOp:rotateXYZ") or xformable.AddRotateXYZOp()
            r_op.Set(Gf.Vec3f(-60.0, 0.0, 0.0))
        except Exception as exc:
            carb.log_warn(f"[my_rapidex] Could not position camera: {exc}")

    def _update_cesium_georeference(self, lat, lon, height=0.0):
        try:
            from cesium.omniverse.usdUtils.usdUtils import get_or_create_cesium_georeference
            georef = get_or_create_cesium_georeference()
            georef.GetGeoreferenceOriginLatitudeAttr().Set(float(lat))
            georef.GetGeoreferenceOriginLongitudeAttr().Set(float(lon))
            georef.GetGeoreferenceOriginHeightAttr().Set(float(height))
        except Exception as exc:
            carb.log_warn(f"[my_rapidex] Could not update Cesium georeference: {exc}")