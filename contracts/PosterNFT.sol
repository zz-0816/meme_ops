// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title PosterNFT
 * @notice ERC-721 NFT 合约，用于将 Meme 分析海报铸造为链上 NFT
 * @dev Solidity 0.8.20+ 编译时 EVM Version 必须设为 paris（避免 PUSH0 不兼容）
 *
 * 部署步骤：
 *   1. Remix → Solidity Compiler → Advanced Config → EVM Version = paris
 *   2. Environment = Injected Provider - MetaMask（确保钱包已解锁且切换至测试链）
 *   3. Deploy（传入合约名称和符号）
 */
contract PosterNFT is ERC721, Ownable {
    // ============ 状态变量 ============

    uint256 private _nextTokenId;
    mapping(uint256 => string) private _tokenURIs;

    // ============ 事件 ============

    event PosterMinted(
        uint256 indexed tokenId,
        address indexed minter,
        string tokenURI
    );

    // ============ 构造函数 ============

    constructor(
        string memory name_,
        string memory symbol_
    ) ERC721(name_, symbol_) Ownable(msg.sender) {}

    // ============ 核心函数 ============

    /**
     * @notice 铸造一张海报 NFT
     * @param tokenURI_ 海报元数据 URI（IPFS 或链上 JSON）
     * @return tokenId 新铸造的 Token ID
     *
     * 用户调用此函数 → MetaMask 弹窗确认 → 链上铸造
     */
    function mint(
        string memory tokenURI_
    ) external returns (uint256) {
        uint256 tokenId = _nextTokenId;
        _nextTokenId++;

        _safeMint(msg.sender, tokenId);
        _tokenURIs[tokenId] = tokenURI_;

        emit PosterMinted(tokenId, msg.sender, tokenURI_);
        return tokenId;
    }

    // ============ 只读函数 ============

    /**
     * @notice 获取 Token 的元数据 URI
     */
    function tokenURI(
        uint256 tokenId
    ) public view override returns (string memory) {
        _requireOwned(tokenId);
        return _tokenURIs[tokenId];
    }

    /**
     * @notice 当前已铸造的 NFT 总数
     */
    function totalSupply() external view returns (uint256) {
        return _nextTokenId;
    }

    /**
     * @notice 查询某个地址拥有的所有 Token ID
     * @dev 仅用于前端展示，不适合 on-chain 逻辑使用（Gas 高）
     */
    function tokensOfOwner(
        address owner
    ) external view returns (uint256[] memory) {
        uint256 balance = balanceOf(owner);
        uint256[] memory tokens = new uint256[](balance);
        uint256 index = 0;
        for (uint256 i = 0; i < _nextTokenId; i++) {
            if (_ownerOf(i) == owner) {
                tokens[index] = i;
                index++;
            }
        }
        return tokens;
    }
}
