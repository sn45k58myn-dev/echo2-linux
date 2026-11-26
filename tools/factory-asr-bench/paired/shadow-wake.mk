/tmp/shadow-wake: /lab/shadow-wake.cpp src/adapter/wake_engine_onnx.cpp
	$(CROSS_COMPILE)g++ $(WAKE_ARM_CXXFLAGS) $^ -static -static-libstdc++ -static-libgcc -Wl,--gc-sections -o $@ -Wl,--start-group $(WAKE_ORT_ARCHIVES) $(WAKE_ORT_ABSEIL) $(RE2_ARCHIVE) -Wl,--end-group -lpthread -ldl -lm
