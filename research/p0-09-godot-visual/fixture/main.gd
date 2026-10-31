extends Control

@onready var background: ColorRect = $Background
@onready var status_label: Label = $Panel/Status
@onready var click_button: Button = $Panel/ClickButton

var click_count: int = 0

func _ready() -> void:
	click_button.pressed.connect(_on_click_button_pressed)
	print("AMODEX_P0_09_WINDOWED_READY")

func _on_click_button_pressed() -> void:
	click_count += 1
	status_label.text = "INPUT_OK"
	click_button.text = "CLICKED"
	background.color = Color("184f4a")
	print("AMODEX_P0_09_INPUT_OK count=%d" % click_count)
