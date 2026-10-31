extends SceneTree

func _init() -> void:
	var values: Array[int] = [6, 7]
	var result := values[0] * values[1]
	if result != 42:
		push_error("AMODEX_P0_08_HEADLESS_TEST_FAIL")
		quit(1)
		return
	print("AMODEX_P0_08_HEADLESS_TEST_OK value=%d" % result)
	quit(0)
