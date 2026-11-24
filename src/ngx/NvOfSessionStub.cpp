// NvOfSession stand-in for builds without the NVIDIA Optical Flow SDK.
//
// The SDK's D3D12 header (nvOpticalFlowD3D12.h) is only available from NVIDIA's
// developer download, so CI builds (and AMD-only builds) compile this instead of
// NvOfSession.cpp. Every entry point reports that NVOF is unavailable. EnhanceGraph
// already switches an AMD adapter from the NVIDIA flow backend to FidelityFX flow
// (see EnhanceGraph.cpp), so this path is never exercised on a Radeon.
#include "veyra/ngx/NvOfSession.h"

namespace veyra::ngx {

NvOfSession::~NvOfSession() { shutdown(); }

bool NvOfSession::initialize(ID3D12Device*, ID3D12Resource*, ID3D12Resource*, ID3D12Resource*,
                             ID3D12Resource*, const Desc&, Status& status) {
    status = Status::LoadLibraryFailure;  // nvofapi64.dll / SDK not part of this build
    return false;
}

bool NvOfSession::execute(uint64_t, Status& status) {
    status = Status::DeviceFailure;
    return false;
}

bool NvOfSession::unregisterAll(Status& status) {
    status = Status::Ok;
    return true;
}

void NvOfSession::shutdown() {}

}  // namespace veyra::ngx
