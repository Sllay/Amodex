extends Node

func _ready() -> void:
	var value := deterministic_sum(19, 23)
	if value != 42:
		push_error("AMODEX_P0_08_RUNTIME_FAIL")
		get_tree().quit(1)
		return
	print("AMODEX_P0_08_BOOT_OK value=%d" % value)
	get_tree().quit(0)

func deterministic_sum(a: int, b: int) -> int:
	return a + b
